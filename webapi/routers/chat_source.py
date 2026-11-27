"""Citation excerpt for the source panel: /chat/source (moved from main.py, S7)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

import config
from rag_core import answer_cache, answer_evidence
from rag_core.approved_qa import load_approved_qa
from rag_core.audit_log import set_audit_actor
from rag_core.mail_access import is_private_mail_record
from rag_core.retrieval import normalize_tenant_id
from webapi.api_auth import ApiAuthContext, enforce_tenant_authorization
from webapi.chat_support import _resolve_staging_collection
from webapi.rate_limit import require_api_auth_rate_limited

router = APIRouter()


# --- Citation excerpt for the source panel (stage 10) ---------------------------


def _approved_pair_qa_id(chunk_id: str, tenant: str) -> Optional[str]:
    # "approved_qa_pair:<qa_id>" (default tenant, or the form written before tenant prefixes) or
    # "<tenant>:approved_qa_pair:<qa_id>". The prefix of another tenant is not accepted.
    head, marker, qa_id = str(chunk_id).partition("approved_qa_pair:")
    if not marker or not qa_id:
        return None
    if head and head != f"{tenant}:":
        return None
    return qa_id


def _approved_source_row(chunk_id: str, tenant: str) -> Optional[Dict[str, Any]]:
    # An approved-QA pair id that is not in the keyword corpus: the
    # approved question and answer themselves (same tenant only).
    if not getattr(config, "APPROVED_QA_ENABLED", False):
        return None
    qa_id = _approved_pair_qa_id(chunk_id, tenant)
    if qa_id is None:
        return None
    loaded = load_approved_qa(str(config.APPROVED_QA_PATH), tenant_id=tenant)
    for answer in loaded.records:
        if answer.qa_id == qa_id:
            first = answer.approved_citations[0] if answer.approved_citations else None
            return {
                "tenant_id": answer.tenant_id,
                "text": f"Q: {answer.question}\nA: {answer.approved_answer}",
                "title": "承認済み回答",
                "source_doc": first.source_doc if first else None,
                "source_pages": list(first.source_pages) if first else [],
                "section_path": [],
                "doc_version": answer.doc_version,
            }
    return None


@router.get("/chat/source")
def chat_source(
    chunk_id: str,
    tenant_id: Optional[str] = None,
    collection: Optional[str] = None,
    request_id: Optional[str] = Query(None, max_length=128),
    number: Optional[int] = Query(None, ge=1, le=100),
    api_auth: ApiAuthContext = Depends(require_api_auth_rate_limited),
):
    # The text of one cited chunk, only when it belongs to the caller's
    # (authorized) tenant; anything else is 404. collection: a staging
    # collection, checked exactly like the chat request that cited it.
    # Approved-QA citations fall back to the approved answer itself.
    tenant = normalize_tenant_id(tenant_id)
    enforce_tenant_authorization(api_auth, tenant)
    set_audit_actor(api_auth)
    staging = _resolve_staging_collection(collection, tenant_id=tenant)
    from rag_core import retrieval

    row = retrieval.chunk_row(chunk_id, staging) if staging else retrieval.chunk_row(chunk_id)
    if not row and not staging:
        row = _approved_source_row(chunk_id, tenant)
    if not row or normalize_tenant_id(row.get("tenant_id")) != tenant or is_private_mail_record(row, chunk_id):
        raise HTTPException(status_code=404, detail="source not found")
    text = str(row.get("display_text") or row.get("text") or "")
    limit = 1500
    out = {
        "chunk_id": str(chunk_id),
        "source_doc": row.get("source_doc"),
        "title": row.get("title") or row.get("source_doc"),
        "source_pages": row.get("source_pages") or [],
        "section_path": [str(p) for p in (row.get("section_path") or [])][:6],
        "doc_version": row.get("doc_version"),
        "text": text[:limit] + ("…" if len(text) > limit else ""),
    }
    if request_id:
        out.update(_version_check(request_id, tenant, chunk_id, number, row, access_scope=answer_cache.access_scope_digest(api_auth), collection=staging))
    return out


def _version_check(request_id: str, tenant: str, chunk_id: str, number: Optional[int],
                   row: Dict[str, Any], *, access_scope: Optional[str] = None, collection: Optional[str] = None) -> Dict[str, Any]:
    """Compare the row shown now with the version the answer of request_id cited (2026-11-27).

    stale: True when the cited text changed since the answer, False when unchanged, None when unknown (no evidence
    record for this request and tenant, the chunk was not cited by it, or the cited version was not recorded)."""
    current_generation = row.get("index_generation") or answer_evidence.active_generation_id()
    result: Dict[str, Any] = {"request_id": request_id, "evidence_found": False, "stale": None,
                              "index_generation": current_generation}
    record = answer_evidence.lookup_evidence(request_id, tenant, access_scope=access_scope)
    if record is None or (record.get("collection") or None) != (collection or None):
        return result
    result["evidence_found"] = True
    entry = answer_evidence.cited_entry(record, number=number, chunk_id=None if number is not None else chunk_id)
    if entry is None or (chunk_id != entry.get("chunk_id") and chunk_id not in (entry.get("member_chunk_ids") or [])):
        result["cited"] = False
        return result
    result["cited"] = True
    result["cited_index_generation"] = entry.get("index_generation") or record.get("active_generation_id")
    # The row hash taken when the answer was built (the same row shown here). chunk_sha256 is the hash of the cited
    # block, which may be merged or parent-expanded text, so it is not compared with a single row.
    stored = entry.get("source_row_sha256")
    if stored and chunk_id == entry.get("chunk_id"):
        result["stale"] = stored != answer_evidence.sha256_text(row.get("text") or "")
    return result
