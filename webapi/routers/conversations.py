"""Conversation history: /chat/threads (moved from main.py, S7)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

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
    tenant_id: Optional[str] = None


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
    if not str(req.thread_id or "").strip():
        raise HTTPException(status_code=400, detail="thread_id is required")
    record = conversation_store.append_turn(
        tenant,
        _conversation_identity(api_auth),
        req.thread_id,
        question=req.question or "",
        answer_text=req.answer_text or "",
        answer_mode=req.answer_mode,
        abstained=bool(req.abstained),
        citations_count=int(req.citations_count or 0),
    )
    return {"ok": True, "thread_id": record["thread_id"]}


@router.delete("/chat/threads/{thread_id}")
def delete_chat_thread(thread_id: str, api_auth: ApiAuthContext = Depends(require_api_auth_rate_limited), tenant_id: Optional[str] = None):
    _require_conversation_history()
    tenant = normalize_tenant_id(tenant_id)
    enforce_tenant_authorization(api_auth, tenant)
    set_audit_actor(api_auth)
    enforce_role(api_auth, ROLE_USER)
    return {"ok": True, "deleted": conversation_store.delete_thread(tenant, _conversation_identity(api_auth), thread_id)}
