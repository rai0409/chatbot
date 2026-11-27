"""Batch mail adapter for the existing canonical ingest contract.

No vectorstore or job-registry side effects. The original source emails remain
unchanged; restored quoted mail is not indexed as an independent original.

Knobs:
  MAIL_CONVERT_TIMEOUT_SECONDS   isolated mail conversion time limit (default 300;
                                 0 = convert in the calling process)
  MAIL_CONVERT_MAX_MEMORY_MB     address-space limit of the mail worker (default 2048; 0 = none)
  MAIL_KEEP_LATEST_ONLY          default false; see mail_ingest.process(keep_latest_only)
  MAIL_SKETCH_MAX_SHINGLES       hashed 4-grams kept per message in the state store (default 512)
"""
from __future__ import annotations

import functools
import hashlib
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from rag_core import mail_access, mail_ingest
from scripts.mail_to_canonical_jsonl import _canonical_rows, sha

_MAIL_TYPES = frozenset({".eml", ".csv", ".tsv", ".xlsx", ".xlsm"})
_TENANT_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _positive_limit(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        raise ValueError(f"invalid_{name.lower()}") from None
    if value < 1:
        raise ValueError(f"invalid_{name.lower()}")
    return value


def validate_mail_inputs(paths: Sequence[Path], *, tenant_id: str) -> None:
    """Bound input count/size/type before accepting or queueing a mail job."""
    if not _TENANT_RE.fullmatch(tenant_id):
        raise ValueError("invalid_mail_tenant_id")
    if not paths:
        raise ValueError("mail_inputs_required")
    max_files = _positive_limit("MAIL_INGEST_MAX_FILES", 100)
    max_bytes = _positive_limit("MAIL_INGEST_MAX_TOTAL_BYTES", 50 * 1024 * 1024)
    max_file = _positive_limit("MAIL_INGEST_MAX_FILE_BYTES", 15 * 1024 * 1024)
    if len(paths) > max_files:
        raise ValueError("mail_file_count_limit")
    total = 0
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_symlink() or not path.is_file():
            raise ValueError("mail_input_must_be_regular_file")
        if path.suffix.lower() not in _MAIL_TYPES:
            raise ValueError("unsupported_mail_format")
        size = path.stat().st_size
        total += size
        if size > max_file or total > max_bytes:
            raise ValueError("mail_input_size_limit")


_PRIVATE_FIELDS = ("_acl_addresses", "_sender_address", "_mail_state", "_key",
                   "_superseded_by_key", "_quote_ref_keys")


def _flag(name: str) -> bool:
    return str(os.getenv(name, "") or "").strip().lower() in {"1", "true", "yes", "on"}


def _nonneg_float(name: str, default: float) -> float:
    try:
        value = float(str(os.getenv(name, "") or "").strip() or default)
    except ValueError:
        raise ValueError(f"invalid_{name.lower()}") from None
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"invalid_{name.lower()}")
    return value


def mail_convert_timeout() -> float:
    return _nonneg_float("MAIL_CONVERT_TIMEOUT_SECONDS", 300.0)


def mail_convert_memory_bytes() -> int:
    return int(_nonneg_float("MAIL_CONVERT_MAX_MEMORY_MB", 2048.0) * 1024 * 1024)


def _salted_hash(salt: bytes, value: str) -> str:
    return hashlib.blake2b(value.encode("utf-8"), key=salt[:64], digest_size=8).hexdigest()


def state_salt(key: bytes, tenant_id: str) -> bytes:
    """Per-tenant salt for hashed shingles/participants (derived; not the ACL key)."""
    return mail_access.derived_secret(key, "shingle:" + tenant_id)


def _convert_local(paths: Sequence[str], *, tenant_id: str, mailbox_owner: str = "",
                   prior: Optional[list] = None, salt_hex: str = "",
                   keep_latest_only: bool = False, overrides: Optional[dict] = None, sheet: str = "",
                   identity_root: str = "/", validate: bool = True,
                   ) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """The parsing part. Runs in the isolated worker (no ACL key there): rows
    keep private plaintext participant fields for the parent to digest.

    overrides / sheet / identity_root / validate=False serve the offline CLI (directory input, column overrides;
    its own size limits are checked by the caller)."""
    paths = [Path(p) for p in paths]
    if validate:
        validate_mail_inputs(paths, tenant_id=tenant_id)
    max_messages = _positive_limit("MAIL_INGEST_MAX_MESSAGES", 2000)
    failures: list[dict] = []
    raws = []
    for path in paths:
        # mail_ingest deliberately raises SystemExit on invalid table columns;
        # present a safe error instead of terminating an API worker.
        try:
            parsed = mail_ingest.iter_inputs(path, dict(overrides or {}), sheet=sheet, failures=failures)
        except SystemExit:
            raise ValueError("invalid_mail_input_format") from None
        if failures:
            if any(str(f.get("message") or "").startswith(("mime_part_limit", "mime_depth_limit")) for f in failures):
                raise ValueError("mail_mime_limit")
            raise ValueError("mail_parse_failure")
        raws.extend(parsed)
        if len(raws) > max_messages:
            raise ValueError("mail_message_count_limit")
    if not raws:
        raise ValueError("mail_batch_empty")

    hasher = functools.partial(_salted_hash, bytes.fromhex(salt_hex)) if salt_hex else None
    result = mail_ingest.process(
        raws, model=None, profile=mail_ingest.PROFILES["evidence"],
        threshold=0.9, smooth=0.0, sentence_split=False,
        match_threshold=0.6, jobs=1, failures=failures,
        participants_extra=[mailbox_owner] if mailbox_owner else (),
        prior=prior, hasher=hasher, keep_latest_only=keep_latest_only,
    )
    if failures or result.failures:
        raise ValueError("mail_processing_failure")
    cap = _positive_limit("MAIL_SKETCH_MAX_SHINGLES", 512)
    # Path("/") is only the fallback basis; identities use Message-ID or a
    # content-based origin (never a file name alone).
    return _canonical_rows(result, tenant_id, Path(identity_root), hasher=hasher,
                           keep_latest_only=keep_latest_only, sketch_cap=cap)


def finalize_acl_rows(rows: Iterable[dict[str, Any]], key: bytes, key_id: str) -> list[dict[str, Any]]:
    """Replace private plaintext participant fields by HMAC digests (ACL fields).

    Called in the parent process (the ACL key never enters the parsing worker).
    """
    out = []
    for row in rows:
        row = dict(row)
        addresses = row.pop("_acl_addresses", None) or []
        sender = row.pop("_sender_address", "") or ""
        for name in _PRIVATE_FIELDS:
            row.pop(name, None)
        row["acl_principals"] = mail_access.principal_digests(addresses, key)
        row["acl_version"] = mail_access.ACL_VERSION
        row["acl_key_id"] = key_id
        row["sender_principal"] = mail_access.principal_digest(sender, key) if sender else ""
        out.append(row)
    return out


@dataclass
class MailBatch:
    rows: list[dict[str, Any]]
    summary: dict[str, int]
    state: list[dict[str, Any]] = field(default_factory=list)   # per-row entries for the mail state store


def _normalize_owner(mailbox_owner: Optional[str]) -> str:
    if mailbox_owner is None or not str(mailbox_owner).strip():
        return ""
    owner = mail_access.normalize_address(mailbox_owner)
    if not owner:
        raise ValueError("invalid_mailbox_owner")
    return owner


def convert_mail_batch(paths: Sequence[Path], *, tenant_id: str, mailbox_owner: Optional[str] = None,
                       prior: Optional[list] = None, keep_latest_only: Optional[bool] = None,
                       isolate: bool = False) -> MailBatch:
    """Convert one explicit batch into ACL'd canonical rows plus state-store entries.

    isolate=True runs the parsing in a separate process (time and memory limited,
    secret environment variables removed). In production without
    MAIL_ACL_HMAC_KEY ingestion is refused (ValueError mail_acl_key_required).
    """
    validate_mail_inputs(paths, tenant_id=tenant_id)
    key, key_id = mail_access.acl_key()
    owner = _normalize_owner(mailbox_owner)
    latest = _flag("MAIL_KEEP_LATEST_ONLY") if keep_latest_only is None else bool(keep_latest_only)
    payload = {"paths": [str(p) for p in paths], "tenant_id": tenant_id, "mailbox_owner": owner,
               "prior": list(prior or []), "salt_hex": state_salt(key, tenant_id).hex(),
               "keep_latest_only": latest}
    timeout = mail_convert_timeout() if isolate else 0.0
    if timeout > 0:
        from rag_core.document_converters import isolated

        try:
            rows, summary = isolated.run_task("mail_batch", payload, timeout=timeout,
                                              memory_limit=mail_convert_memory_bytes())
        except isolated.ConversionFailed as exc:
            if exc.error_type == "ValueError":
                raise ValueError(str(exc)) from None
            raise
    else:
        rows, summary = _convert_local(**payload)
    state = []
    for row in rows:
        st = dict(row.get("_mail_state") or {})
        state.append(st)
    rows = finalize_acl_rows(rows, key, key_id)
    entries = []
    for row, st in zip(rows, state):
        entries.append({
            "source_doc": row["source_doc"], "row_ids": [row["id"]],
            "message_id_h": sha(tenant_id + "\n" + row["message_id"])[:32] if row.get("message_id") else "",
            "date_iso": row.get("date_iso") or "", "content_sha256": row["content_sha256"],
            "sender_principal": row.get("sender_principal") or "", "acl_key_id": key_id,
            "quote_refs": list(row.get("quote_refs") or []),
            "superseded_by": row.get("superseded_by") or "",
            "sh": st.get("sh") or [], "n": int(st.get("n") or 0),
            "participants_h": st.get("participants_h") or [],
        })
    return MailBatch(rows, summary, entries)


def convert_mail_inputs(paths: Sequence[Path], *, tenant_id: str, mailbox_owner: Optional[str] = None,
                        keep_latest_only: Optional[bool] = None,
                        isolate: bool = True) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Convert an explicitly authorized set of files in a *single* mail batch.

    Authorization and INGEST_ALLOWED_ROOTS confinement are enforced by the
    ingestion job before calling this function. No directory walk is accepted
    here: batch boundaries are explicit and countable. Parsing runs in the isolated worker by default
    (like the API job); isolate=False parses in this process (debugging only).
    """
    batch = convert_mail_batch(paths, tenant_id=tenant_id, mailbox_owner=mailbox_owner,
                               keep_latest_only=keep_latest_only, isolate=isolate)
    return batch.rows, batch.summary


# --- update safety ------------------------------------------------------------


def apply_update_policy(rows: Sequence[dict[str, Any]], existing: Mapping[str, Mapping[str, Any]],
                        ) -> tuple[list[dict[str, Any]], dict[str, int], set[str]]:
    """Decide per row against the already ingested state (by source_doc).

    Returns (rows to write, counts, source_docs replaced). Fail-closed:
      identical content            -> skipped_identical
      different sender digest      -> conflict_same_message_id (Message-ID can be forged)
      older or undated             -> skipped_older / conflict_same_message_id
      same date, different content -> conflict_same_message_id (existing kept)
      newer, same sender           -> replaced_newer
    """
    counts = {"new": 0, "skipped_identical": 0, "skipped_older": 0,
              "conflict_same_message_id": 0, "replaced_newer": 0}
    keep: list[dict[str, Any]] = []
    replaced: set[str] = set()
    for row in rows:
        old = existing.get(row["source_doc"])
        if not old:
            counts["new"] += 1
            keep.append(row)
            continue
        if old.get("content_sha256") == row.get("content_sha256"):
            counts["skipped_identical"] += 1
            continue
        if not old.get("sender_principal") or old.get("sender_principal") != row.get("sender_principal"):
            counts["conflict_same_message_id"] += 1
            continue
        new_dt = mail_ingest.date_utc(str(row.get("date_iso") or ""))
        old_dt = mail_ingest.date_utc(str(old.get("date_iso") or ""))
        if new_dt is None or old_dt is None or new_dt == old_dt:
            counts["conflict_same_message_id"] += 1
            continue
        if new_dt < old_dt:
            counts["skipped_older"] += 1
            continue
        counts["replaced_newer"] += 1
        replaced.add(row["source_doc"])
        keep.append(row)
    return keep, counts, replaced


# --- deletion -----------------------------------------------------------------


def mail_source_doc(tenant_id: str, *, message_id: str = "") -> str:
    """The source_doc of a message identified by Message-ID (same derivation as ingestion)."""
    mid = str(message_id or "").strip().strip("<>")
    if not mid:
        raise ValueError("message_id_required")
    return "mail:" + sha(tenant_id + "\n" + "message-id:" + mid)[:32]


def plan_mail_deletion(state: Mapping[str, Mapping[str, Any]], tenant_id: str, *,
                       source_doc: str = "", message_id: str = "") -> dict[str, Any]:
    """Which chunk ids/source_docs to delete, plus rows whose quotes reference it
    (those lost the quoted text to a reference and should be re-ingested)."""
    if not _TENANT_RE.fullmatch(tenant_id):
        raise ValueError("invalid_mail_tenant_id")
    doc = str(source_doc or "").strip() or mail_source_doc(tenant_id, message_id=message_id)
    if not doc.startswith("mail:"):
        raise ValueError("not_a_mail_source_doc")
    entry = state.get(doc) or {}
    dependents = sorted(d for d, e in state.items() if d != doc and doc in (e.get("quote_refs") or []))
    superseded = sorted(d for d, e in state.items() if e.get("superseded_by") == doc)
    return {"source_doc": doc, "found": bool(entry), "ids": list(entry.get("row_ids") or []),
            "dependent_source_docs": dependents, "superseded_source_docs": superseded}
