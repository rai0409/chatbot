"""Fail-closed private email classification while per-user ACL is unavailable.

This intentionally grants *no* mail access through user-facing retrieval.
The ingestion/admin staging workflow remains separate. Never interpret a
mail sender, recipient, tenant, or API-key fingerprint as a read grant.
"""
from __future__ import annotations

from collections.abc import Mapping


def is_private_mail_record(meta: Mapping | None) -> bool:
    if not isinstance(meta, Mapping):
        return False
    for field in ("type", "source_type", "doc_type"):
        if str(meta.get(field) or "").strip().casefold() == "email":
            return True
    for field in ("source_doc", "doc_id"):
        if str(meta.get(field) or "").strip().casefold().startswith("mail:"):
            return True
    # The canonical email identifier embeds :email: even if a downstream
    # producer accidentally loses type and source metadata.
    return ":email:" in str(meta.get("id") or "").casefold()
