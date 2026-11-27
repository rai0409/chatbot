"""Conversation history: /chat/threads (moved from main.py, S7)."""
from __future__ import annotations

import re
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StrictStr

from rag_core import answer_cache, answer_evidence
from rag_core.audit_log import set_audit_actor
from rag_core.retrieval import normalize_tenant_id
from webapi import conversation_store
from webapi import production_guard
from webapi.api_auth import ApiAuthContext, enforce_tenant_authorization
from webapi.rate_limit import require_api_auth_rate_limited
from webapi.rbac import ROLE_USER, enforce_role

router = APIRouter()


# --- Conversation history (default-off; per-caller, tenant/identity-scoped) ---


class ConversationTurnRequest(BaseModel):
    thread_id: str
    question: str
    answer_text: Optional[str] = ""
    answer_mode: Optional[str] = None
    abstained: Optional[bool] = False
    citations_count: Optional[int] = 0
    source_docs: Optional[List[StrictStr]] = None  # client-provided metadata, not authorization evidence
    tenant_id: Optional[str] = None
    # The server request id of the answer (/chat response request_id). When its evidence record exists for this
    # tenant, answer_mode / citations_count / source_docs are taken from it and the client values are ignored.
    request_id: Optional[str] = Field(None, max_length=128)


def _server_turn_fields(request_id: Optional[str], tenant: str, answer_text: str, *, access_scope: Optional[str] = None) -> Optional[dict]:
    record = answer_evidence.lookup_evidence(request_id, tenant, access_scope=access_scope) if request_id else None
    if record is None or record.get("answer_sha256") != answer_evidence.sha256_text(answer_text):
        return None
    cited = [c for c in record.get("cited") or [] if isinstance(c, dict)]
    docs: List[str] = []
    for entry in cited:
        doc = str(entry.get("source_doc") or "").strip()
        if doc and len(doc) <= 512 and doc not in docs:
            docs.append(doc)
    return {"answer_mode": record.get("answer_mode"), "citations_count": len(cited) if docs else 0,
            "source_docs": docs[:20]}


def _conversation_identity(api_auth: ApiAuthContext) -> str:
    # Scope history to the authenticated caller; never a raw key. With API auth
    # off there is no per-user identity, so all callers share "anonymous".
    if isinstance(api_auth, ApiAuthContext) and api_auth.key_fingerprint:
        return api_auth.key_fingerprint
    if production_guard.is_production():
        # Without a per-user identity every caller would share one history.
        raise HTTPException(status_code=403, detail="conversation history requires an authenticated user")
    return "anonymous"


def _require_conversation_history() -> None:
    # Default-off: the feature adds runtime surface, so when disabled the routes
    # behave as if absent (404), leaving default behavior unchanged.
    if not conversation_store.conversation_history_enabled():
        raise HTTPException(status_code=404, detail="not found")


def validate_thread_id(thread_id: str) -> str:
    if not isinstance(thread_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", thread_id):
        raise HTTPException(status_code=400, detail="invalid thread_id")
    return thread_id


@router.get("/chat/threads")
def list_chat_threads(api_auth: ApiAuthContext = Depends(require_api_auth_rate_limited), tenant_id: Optional[str] = None):
    _require_conversation_history()
    tenant = normalize_tenant_id(tenant_id)
    enforce_tenant_authorization(api_auth, tenant)
    set_audit_actor(api_auth)
    return {"threads": conversation_store.list_threads(tenant, _conversation_identity(api_auth))}


@router.get("/chat/threads/{thread_id}")
def get_chat_thread(thread_id: str, api_auth: ApiAuthContext = Depends(require_api_auth_rate_limited), tenant_id: Optional[str] = None):
    _require_conversation_history()
    validate_thread_id(thread_id)
    tenant = normalize_tenant_id(tenant_id)
    enforce_tenant_authorization(api_auth, tenant)
    set_audit_actor(api_auth)
    return {"thread_id": thread_id, "turns": conversation_store.get_thread(tenant, _conversation_identity(api_auth), thread_id)}


@router.post("/chat/threads")
def append_chat_thread(req: ConversationTurnRequest, api_auth: ApiAuthContext = Depends(require_api_auth_rate_limited)):
    _require_conversation_history()
    tenant = normalize_tenant_id(req.tenant_id)
    enforce_tenant_authorization(api_auth, tenant)
    set_audit_actor(api_auth)
    enforce_role(api_auth, ROLE_USER)
    validate_thread_id(req.thread_id)
    server = _server_turn_fields(req.request_id, tenant, req.answer_text or "", access_scope=answer_cache.access_scope_digest(api_auth))
    if server is not None:
        # Server evidence wins: redact_source_doc then works on what was really cited.
        answer_mode, citations_count, source_docs = server["answer_mode"], server["citations_count"], server["source_docs"]
        provenance = "server"
    else:
        try:
            source_docs = conversation_store._clean_source_docs(req.source_docs)
            if int(req.citations_count or 0) > 0 and not source_docs:
                raise ValueError("citations require source_docs")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid source_docs") from exc
        answer_mode, citations_count, provenance = req.answer_mode, int(req.citations_count or 0), "client"
    record = conversation_store.append_turn(
        tenant,
        _conversation_identity(api_auth),
        req.thread_id,
        question=req.question or "",
        answer_text=req.answer_text or "",
        answer_mode=answer_mode,
        abstained=bool(req.abstained),
        citations_count=citations_count,
        source_docs=source_docs,
        provenance=provenance,
        request_id=req.request_id if server is not None else None,
    )
    return {"ok": True, "thread_id": record["thread_id"], "provenance": provenance}


@router.delete("/chat/threads/{thread_id}")
def delete_chat_thread(thread_id: str, api_auth: ApiAuthContext = Depends(require_api_auth_rate_limited), tenant_id: Optional[str] = None):
    _require_conversation_history()
    validate_thread_id(thread_id)
    tenant = normalize_tenant_id(tenant_id)
    enforce_tenant_authorization(api_auth, tenant)
    set_audit_actor(api_auth)
    enforce_role(api_auth, ROLE_USER)
    return {"ok": True, "deleted": conversation_store.delete_thread(tenant, _conversation_identity(api_auth), thread_id)}
