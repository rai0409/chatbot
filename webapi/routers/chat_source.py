"""Citation excerpt for the source panel: /chat/source (moved from main.py, S7)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException

import config
from rag_core.approved_qa import load_approved_qa
from rag_core.audit_log import set_audit_actor
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
    if not row or normalize_tenant_id(row.get("tenant_id")) != tenant:
        raise HTTPException(status_code=404, detail="source not found")
    text = str(row.get("display_text") or row.get("text") or "")
    limit = 1500
    return {
        "chunk_id": str(chunk_id),
        "source_doc": row.get("source_doc"),
        "title": row.get("title") or row.get("source_doc"),
        "source_pages": row.get("source_pages") or [],
        "section_path": [str(p) for p in (row.get("section_path") or [])][:6],
        "doc_version": row.get("doc_version"),
        "text": text[:limit] + ("…" if len(text) > limit else ""),
    }
