from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler as _default_http_exception_handler
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from rag_core import answer_cache
from rag_core import approved_similar
from rag_core import approved_similar_feature_reranker
from rag_core.qa import distance_guard_calibration
from rag_core.retrieval import keyword_index_status
from rag_core.review_actions import (
    ALLOWED_ACTION_TYPES,
    ALLOWED_STATUS_AFTER,
    append_review_action_event,
    build_review_action_event,
)
from webapi import ingestion_jobs
from webapi import metrics_registry
from webapi.admin_auth import (
    require_admin_auth,
    require_scope,
)
from rag_core import integrations, llm_gateway
from webapi.routers import access as access_router
from webapi.routers import audit as audit_router
from webapi.routers import console as console_router
from webapi.routers import contracts as contracts_router
from webapi.routers import extraction as extraction_router
from webapi.routers import golden as golden_router
from webapi.routers import governance as governance_router
from webapi.routers import ingestion as ingestion_router
from webapi.routers.ingestion import (
    IngestionDryRunRequest,
    RawDocumentIngestionRequest,
    _UPLOAD_PRUNE,
    _prune_old_uploads,
    ingestion_dry_run,
    ingestion_job_status,
    ingestion_jobs_list,
    ingestion_page,
    ingestion_raw_documents,
)
from webapi.routers import admin_core as admin_core_router
from webapi.routers import auth as auth_router
from webapi.routers import chat as chat_router
from webapi.routers import chat_source as chat_source_router
from webapi.routers import conversations as conversations_router
from webapi.routers import infra as infra_router
from webapi.routers.admin_core import (
    _ADMIN_LOGIN_LIMITER,
    AdminLoginRequest,
    _load_review_items,
    admin_home_page,
    admin_login,
    admin_login_page,
    admin_logout,
    admin_logout_all,
    admin_metrics_summary,
    admin_review_items,
    admin_review_page,
)
from webapi.routers.auth import (
    SessionRevokeRequest,
    oidc_callback,
    oidc_login,
    oidc_logout,
    revoke_sessions,
)
from webapi.routers.chat import (
    _MAX_CONTEXT_CHARS,
    _MAX_QUESTION_CHARS,
    _MAX_TOP_K,
    _PRODUCT_PREVIEW_KEYWORD_PROFILES,
    _PRODUCT_PREVIEW_THRESHOLD_PROFILES,
    ChatRequest,
    ProductFeedbackRequest,
    ProductPreviewChatRequest,
    SearchDebugRequest,
    SearchRequest,
    _approved_exact_chat_lookup,
    _approved_qa_index_for,
    _approved_qa_lookup,
    _embedding_client,
    _is_non_serving_product_profile,
    _product_preview_profile_path,
    _resolve_rag_profile_id,
    _conversation_question,
    chat,
    chat_feedback,
    chat_product_preview,
    chat_stream,
    chat_suggest,
    resolve_chat_runtime_profile,
    search,
    search_debug,
)
from webapi.routers.chat_source import _approved_pair_qa_id, _approved_source_row, chat_source
from webapi.routers.conversations import (
    ConversationTurnRequest,
    _conversation_identity,
    append_chat_thread,
    delete_chat_thread,
    get_chat_thread,
    list_chat_threads,
)
from webapi.routers.infra import (
    _health_details,
    admin_health,
    branding_endpoint,
    chat_ui_page,
    health,
    metrics,
    product_preview_page,
    ready,
    require_metrics_access,
    static_script,
    ui_context,
)
from webapi.chat_support import (
    _bounded_id_list,
    _bounded_text,
    _collection_meta,
    _preview,
    _resolve_staging_collection,
)
from webapi.routers import records as records_router
from webapi.routers import sources as sources_router
from webapi.routers import synonyms as synonyms_router
from webapi.routers import system as system_router
from webapi.routers import usage as usage_router
from webapi import admin_common
from webapi import production_guard
from webapi import state_backend
from starlette.exceptions import HTTPException as StarletteHTTPException
from webapi.security_middleware import HardeningMiddleware


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
# APP_ENV=production refuses to start with unsafe settings (see
# webapi/production_guard.py); development keeps the historical behaviour.
production_guard.check_production_settings()
_DOCS = production_guard.docs_enabled()


@asynccontextmanager
async def _lifespan(_app):
    # Stage 14: jobs of a process that was stopped (deploy, crash) are marked
    # interrupted instead of staying "running" forever; on shutdown the job
    # pool and the conversion workers stop and shared connections close.
    try:
        recovered = ingestion_jobs.recover_interrupted_jobs()
        if recovered:
            logging.warning("ingestion jobs marked interrupted at startup: %d", recovered)
    except Exception:  # noqa: BLE001 - never block startup on the job registry
        logging.exception("ingestion job recovery failed")
    try:
        # Settings are present but the integration is off (the default since the switches exist): say so, once.
        for line in integrations.startup_warnings():
            logging.warning("external integration: %s", line)
    except Exception:  # noqa: BLE001 - never block startup on a diagnostic
        logging.exception("integration check failed")
    try:
        calibration = distance_guard_calibration()
        if not calibration["calibrated"]:
            logging.warning(
                "distance guard thresholds were calibrated for %s, active embedding model is %s: "
                "run scripts/calibrate_distance_guard.py (RAG_DIST_CALIBRATED_MODEL)",
                calibration["calibrated_for"], calibration["active_model"],
            )
    except Exception:  # noqa: BLE001
        logging.exception("distance guard calibration check failed")
    yield
    try:
        ingestion_jobs.shutdown(wait=False)
    finally:
        state_backend.close_all()


app = FastAPI(
    docs_url="/docs" if _DOCS else None,
    redoc_url="/redoc" if _DOCS else None,
    openapi_url="/openapi.json" if _DOCS else None,
    lifespan=_lifespan,
)
# Request body size limit and security headers on every response.
app.add_middleware(HardeningMiddleware)
# The routes that live in routers (webapi/routers), one module per area.
app.include_router(system_router.router)
app.include_router(console_router.router)
for _router_module in (
    records_router, golden_router, usage_router, audit_router, governance_router, access_router,
    ingestion_router, contracts_router, extraction_router, sources_router, synonyms_router,
    infra_router, auth_router, admin_core_router, conversations_router, chat_source_router, chat_router,
):
    app.include_router(_router_module.router)
# The LLM gateway (rag_core/llm_gateway.py): breaker changes go to the usage log, its counters to the metrics.
llm_gateway.install()
llm_gateway.set_metrics_hook(metrics_registry.increment)


def _production_index_checks() -> None:
    # v2_02 P0-1 / P0-3: load the keyword index at start-up (the first query
    # no longer pays for it) and refuse to serve a missing/empty index or one
    # built with another normalization than the one queries use.
    from rag_core.ja_text import active_normalization_version
    from rag_core.retrieval import keyword_index_versions

    status = keyword_index_status()
    if not status.get("keyword_index_loaded"):
        raise RuntimeError(
            "APP_ENV=production refused to start: keyword index is missing or empty "
            f"({status.get('keyword_index_path')})"
        )
    active = active_normalization_version()
    other = {v: n for v, n in keyword_index_versions()["normalization_version"].items() if v and v != active}
    if other:
        raise RuntimeError(
            f"APP_ENV=production refused to start: keyword index rows use normalization {sorted(other)}, "
            f"queries use {active}; rebuild the index or set NORMALIZATION_PROFILE to match"
        )


if production_guard.is_production():
    _production_index_checks()
_ALLOWED_HOSTS = [h.strip() for h in str(os.getenv("ALLOWED_HOSTS", "")).split(",") if h.strip()]
if _ALLOWED_HOSTS:
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_ALLOWED_HOSTS)


def _cors_allow_origins() -> List[str]:
    raw = str(os.getenv("CORS_ALLOW_ORIGINS", ""))
    return [part.strip() for part in raw.split(",") if part.strip()]


_CORS_ORIGINS = _cors_allow_origins()
if _CORS_ORIGINS:
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_CORS_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "Authorization", "X-Api-Key", "X-Admin-Token"],
    )
_STATIC_DIR = Path(__file__).resolve().parent / "static"


class ReviewActionRequest(BaseModel):
    review_id: str
    action_type: str
    feedback_token: Optional[str] = None
    tenant_id: Optional[str] = "default"
    selected_candidate_id: Optional[str] = None
    operator_note: Optional[str] = None
    status_after: Optional[str] = None
    tags: Optional[List[str]] = None


# The helpers every administrative route shares live in webapi/admin_common.py (the routers import them from there);
# these are the old names.
_request_scope = admin_common.request_scope
_admin_actor = admin_common.admin_actor
_audit_admin = admin_common.audit_admin


_ADMIN_PAGES = {"/admin", "/admin/review", "/admin/ingestion", "/admin/synonyms"}


@app.exception_handler(StarletteHTTPException)
async def _http_exception_with_admin_login(request: Request, exc: StarletteHTTPException):
    # A browser opening an admin page without a valid session goes to the
    # login page instead of a JSON 401 (API clients keep the 401).
    path = request.url.path.rstrip("/") or "/"
    if (
        exc.status_code == 401
        and request.method == "GET"
        and path in _ADMIN_PAGES
        and "text/html" in str(request.headers.get("accept") or "")
    ):
        return RedirectResponse(url="/admin/login?next=" + quote(path), status_code=303)
    return await _default_http_exception_handler(request, exc)


@app.exception_handler(state_backend.StateBackendUnavailable)
async def _state_backend_unavailable(request: Request, exc: Exception):
    return JSONResponse(status_code=503, content={"detail": "shared state service unavailable"}, headers={"Retry-After": "5"})


_admin_page_html = admin_common.admin_page_html


@app.post("/admin/review/action")
def admin_review_action(req: ReviewActionRequest, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    review_id = str(req.review_id or "").strip()
    action_type = str(req.action_type or "").strip()
    status_after = str(req.status_after or "").strip() if req.status_after is not None else None
    if not review_id:
        raise HTTPException(status_code=400, detail="review_id is required")
    if action_type not in ALLOWED_ACTION_TYPES:
        raise HTTPException(status_code=400, detail="invalid action_type")
    if status_after is not None and status_after not in ALLOWED_STATUS_AFTER:
        raise HTTPException(status_code=400, detail="invalid status_after")
    queued = [
        item for item in _load_review_items(limit=500)["items"] if str(item.get("review_id") or "") == review_id
    ]
    if queued and all(str(item.get("tenant_id") or "default") != (req.tenant_id or "default") for item in queued):
        raise HTTPException(status_code=403, detail="review item belongs to another tenant")
    # Stage 15: the item's own tenant (the queue), not only the request body.
    scope = _request_scope(request)
    require_scope(scope, req.tenant_id or "default")
    for item in queued:
        require_scope(scope, item.get("tenant_id") or "default")

    event = build_review_action_event(
        review_id=review_id,
        action_type=action_type,
        feedback_token=req.feedback_token,
        tenant_id=req.tenant_id or "default",
        selected_candidate_id=req.selected_candidate_id,
        operator_note=req.operator_note,
        status_after=status_after,
        tags=req.tags,
    )
    stored = append_review_action_event(event)
    _audit_admin(scope, "review_action", tenant_id=req.tenant_id or "default", review_id=review_id,
                 action_type=action_type, status_after=status_after)
    response: Dict[str, Any] = {
        "ok": True,
        "stored": stored,
        "action_id": event["action_id"],
        "review_id": event["review_id"],
        "action_type": event["action_type"],
    }
    if not stored:
        response["warning"] = "review_action_logging_failed"
    return response


# The routes moved to webapi/routers/*; the names below stay reachable as attributes of this module
# (tests and callers use them) and are listed so they count as used.
_REEXPORTED = (
    IngestionDryRunRequest, RawDocumentIngestionRequest, ingestion_dry_run, ingestion_job_status, ingestion_jobs_list,
    ingestion_page, ingestion_raw_documents, _UPLOAD_PRUNE, _prune_old_uploads,
    # S7: infra, auth, admin_core, conversations and chat_source routers.
    _ADMIN_LOGIN_LIMITER, AdminLoginRequest, admin_home_page, admin_login, admin_login_page, admin_logout,
    admin_logout_all, admin_metrics_summary, admin_review_items, admin_review_page, SessionRevokeRequest,
    oidc_callback, oidc_login, oidc_logout, revoke_sessions, _approved_pair_qa_id, _approved_source_row, chat_source,
    ConversationTurnRequest, append_chat_thread, delete_chat_thread, get_chat_thread, list_chat_threads,
    _health_details, admin_health, branding_endpoint, chat_ui_page, health, metrics, product_preview_page, ready,
    require_metrics_access, static_script, ui_context, _conversation_identity,
    _bounded_id_list, _bounded_text, _collection_meta, _preview, _resolve_staging_collection,
    # S7: the chat core (webapi/routers/chat.py).
    _MAX_CONTEXT_CHARS, _MAX_QUESTION_CHARS, _MAX_TOP_K, _PRODUCT_PREVIEW_KEYWORD_PROFILES,
    _PRODUCT_PREVIEW_THRESHOLD_PROFILES, ChatRequest, ProductFeedbackRequest, ProductPreviewChatRequest,
    SearchDebugRequest, SearchRequest, _approved_exact_chat_lookup, _approved_qa_index_for, _approved_qa_lookup,
    _embedding_client, _is_non_serving_product_profile, _product_preview_profile_path, _resolve_rag_profile_id,
    _conversation_question, chat, chat_feedback, chat_product_preview, chat_stream, chat_suggest,
    resolve_chat_runtime_profile, search, search_debug, answer_cache, approved_similar,
    approved_similar_feature_reranker,
)
_admin_label = admin_common.admin_label


def __getattr__(name: str):
    # The approved-QA index cache lives in the chat router (it reassigns it); read it from there so
    # main._approved_qa_index is always the current value.
    if name in ("_approved_qa_index", "_approved_qa_index_path"):
        return getattr(chat_router, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
