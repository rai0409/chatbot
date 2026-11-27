"""Fail-closed private email classification and the per-person mail ACL model.

Today no user-facing retrieval grants mail access: ``is_private_mail_record``
marks every mail chunk and the read paths exclude it. ``MAIL_SEARCH_ENABLED``
stays False until an authenticated, persistent per-person principal is wired
into retrieval. Never interpret a mail sender, recipient, tenant, or API-key
fingerprint as a read grant on its own.

ACL data model (rows produced by ``rag_core.mail_to_canonical``):

  acl_principals  sorted HMAC-SHA256 hex digests of normalized (lowercase)
                  addresses of From/To/Cc (+ the mailbox owner when given).
                  BCC recipients other than the owner are never added.
                  No plaintext address is stored in ACL fields.
  acl_version     ACL_VERSION; unknown versions are never visible.
  acl_key_id      "dev" (fixed development key) or a key fingerprint.

  MAIL_ACL_HMAC_KEY      HMAC key. Required when APP_ENV=production (ingestion
                         is refused without it); development uses a fixed key
                         and marks rows acl_key_id="dev" (never visible in
                         production).
  MAIL_SEARCH_ENABLED    default false: mail stays excluded for everyone.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import unicodedata
from collections.abc import Iterable, Mapping

ACL_VERSION = 1
SUPPORTED_ACL_VERSIONS = frozenset({ACL_VERSION})
DEV_KEY_ID = "dev"
_DEV_KEY = b"mail-acl-development-key-not-for-production"


def _flag(name: str) -> bool:
    return str(os.getenv(name, "") or "").strip().lower() in {"1", "true", "yes", "on"}


# Module flag (read at import; tests/operators may override the attribute).
MAIL_SEARCH_ENABLED: bool = _flag("MAIL_SEARCH_ENABLED")

_MAIL_TYPE_LABELS = frozenset({"email", "mail", "eml"})
# Canonical and legacy mail ids share the email:<message>:h<hash> structure.
# Matched exactly so a general document whose id merely contains ":email:"
# (an xlsx sheet named "email", a PDF with doc_id "email") is not mail.
_MAIL_ID = re.compile(r"(?:[A-Za-z0-9_-]{1,64}:)?email:[^:\s]+:h[0-9a-f]+", re.IGNORECASE)


def is_private_mail_record(meta: Mapping | None, row_id: str | None = None) -> bool:
    if not isinstance(meta, Mapping):
        meta = {}
    for field in ("type", "source_type", "doc_type"):
        if str(meta.get(field) or "").strip().casefold() in _MAIL_TYPE_LABELS:
            return True
    for field in ("source_doc", "doc_id"):
        if str(meta.get(field) or "").strip().casefold().startswith("mail:"):
            return True
    if str(meta.get("parser") or "").strip().casefold().startswith("mail-ingest"):
        return True
    # The canonical email identifier still marks mail even if a downstream
    # producer accidentally loses type and source metadata.
    return any(_MAIL_ID.fullmatch(str(value or "").strip()) for value in (row_id, meta.get("id")))


def vector_columns(result, *fields, query=False):
    """Aligned store columns, or None when correspondence cannot be established."""
    columns = []
    for name in ("ids", *fields):
        column = result.get(name) if isinstance(result, Mapping) else None
        if query:
            if not isinstance(column, (list, tuple)) or len(column) != 1:
                return None
            column = column[0]
        if not isinstance(column, (list, tuple)) or (columns and len(column) != len(columns[0])):
            return None
        if name == "ids" and (any(not isinstance(i, str) or not i.strip() for i in column)
                              or len(set(column)) != len(column)):
            return None
        columns.append(column)
    return columns


def public_vector_row(row_id, meta):
    return (isinstance(row_id, str) and bool(row_id.strip()) and isinstance(meta, Mapping)
            and not is_private_mail_record(meta, row_id) and meta.get("id", row_id) == row_id)


# --- per-person ACL -----------------------------------------------------------


_PROD_ENVS = frozenset({"production", "prod"})
# The fixed development key is usable only when APP_ENV is unset or names a dev
# environment; any other value (a typo, "staging") requires MAIL_ACL_HMAC_KEY.
_DEV_ENVS = frozenset({"", "development", "dev", "test", "local"})


def _app_env() -> str:
    return str(os.getenv("APP_ENV", "")).strip().lower()


def is_production() -> bool:
    return _app_env() in _PROD_ENVS


def normalize_address(address: str | None) -> str:
    value = unicodedata.normalize("NFKC", str(address or "")).strip().strip("<>").strip().lower()
    if value.startswith("mailto:"):
        value = value[7:]
    return value if "@" in value and " " not in value else ""


def _key_bytes(key: bytes | str) -> bytes:
    return key if isinstance(key, bytes) else str(key).encode("utf-8")


def principal_digest(address: str, key: bytes | str) -> str:
    """HMAC-SHA256 hex digest of a normalized address ("" for a non-address)."""
    norm = normalize_address(address)
    if not norm:
        return ""
    return hmac.new(_key_bytes(key), norm.encode("utf-8"), hashlib.sha256).hexdigest()


def principal_digests(addresses: Iterable[str], key: bytes | str) -> list[str]:
    return sorted({d for d in (principal_digest(a, key) for a in addresses) if d})


def acl_key() -> tuple[bytes, str]:
    """(key, key_id). Production without MAIL_ACL_HMAC_KEY refuses ingestion."""
    raw = str(os.getenv("MAIL_ACL_HMAC_KEY", "") or "")
    if raw.strip():
        key = raw.encode("utf-8")
        return key, "k1:" + hmac.new(key, b"mail-acl-key-id", hashlib.sha256).hexdigest()[:12]
    if _app_env() not in _DEV_ENVS:
        raise ValueError("mail_acl_key_required")
    return _DEV_KEY, DEV_KEY_ID


def derived_secret(key: bytes, label: str) -> bytes:
    """A purpose-bound secret derived from the ACL key (the key itself never leaves the parent)."""
    return hmac.new(key, ("mail-derived:" + label).encode("utf-8"), hashlib.sha256).digest()


def mail_visible_to(record: Mapping | None, principals: set[str] | frozenset[str] | None) -> bool:
    """True only for an ACL'd record whose principals intersect the caller's digests.

    Fail-closed: no ACL, an unsupported acl_version, an empty principal set, or a
    development-key row in production are never visible.
    """
    if not isinstance(record, Mapping) or not principals:
        return False
    try:
        version = int(record.get("acl_version"))
    except (TypeError, ValueError):
        return False
    if version not in SUPPORTED_ACL_VERSIONS:
        return False
    acl = record.get("acl_principals")
    if not isinstance(acl, (list, tuple)) or not acl:
        return False
    if is_production() and str(record.get("acl_key_id") or "") in {"", DEV_KEY_ID}:
        return False
    return bool({str(p) for p in acl if p} & {str(p) for p in principals if p})


def mail_record_allowed(record: Mapping | None, principals: set[str] | frozenset[str] | None) -> bool:
    """Non-mail records pass; mail only with MAIL_SEARCH_ENABLED and a matching ACL."""
    if not is_private_mail_record(record):
        return True
    return bool(MAIL_SEARCH_ENABLED) and mail_visible_to(record, principals)


def looks_like_mail_table(header_row: list[str] | None) -> bool:
    """A CSV/XLSX header with mail-export columns (body + sender + to/date/subject).

    Lets the generic document ingestion path refuse a mail export that would
    otherwise become fully searchable without type=email.
    """
    from rag_core.mail_ingest import COLUMN_ALIASES, nfkc

    header = {nfkc(str(h or "")).strip().lower() for h in (header_row or [])}

    def has(name: str) -> bool:
        return any(alias in header for alias in COLUMN_ALIASES[name])

    return has("text") and has("from") and (has("to") or has("date") or has("subject"))
