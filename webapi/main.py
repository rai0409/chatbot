from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exception_handlers import http_exception_handler as _default_http_exception_handler
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

import config
from rag_core import answer_cache
from rag_core import approved_similar
from rag_core import approved_similar_feature_reranker
from rag_core import embedding_provider
from rag_core.approved_qa_exact_lookup import lookup_approved_qa_exact
from rag_core.approved_similar import search_approved_similar_candidates
from rag_core.approved_qa import ApprovedAnswer, ApprovedQAIndex, load_approved_qa, lookup_approved_answer
from rag_core.audit_log import (
    append_audit_event,
    append_feedback_audit_event,
    append_product_preview_chat_audit_event,
)
from rag_core.product_contract import (
    ANSWER_MODE_APPROVED_ALIAS_MATCH,
    ANSWER_MODE_APPROVED_EXACT_MATCH,
    ANSWER_MODE_APPROVED_SIMILAR_CANDIDATE_ONLY,
    ANSWER_MODE_FALLBACK_NO_ANSWER,
    CONFIDENCE_ROUTE_CANDIDATE_ONLY,
    CONFIDENCE_ROUTE_EXACT_MATCH,
    CONFIDENCE_ROUTE_NO_ANSWER,
    build_audit_event,
    build_candidate_contract,
    build_product_answer_envelope,
    generate_feedback_token,
)
from rag_core.feedback_rerank_profile import PROFILE_PATH, apply_feedback_preview_rerank
from rag_core.product_profile import load_product_profile
from rag_core.product_route_policy import build_route_policy
from rag_core import search_mode
from rag_core import suggest as question_suggest
from rag_core import change_ledger as source_changes, source_apply, source_registry, source_sync
from rag_core import extraction_review, extraction_store
from rag_core import contract_notify, contracts
from rag_core.connectors import outlook as outlook_connector
from rag_core.profile_loader import is_valid_rag_profile_id
from rag_core.qa import (
    answer_query_stream,
    answer_query_with_trace,
    debug_retrieve_with_trace,
    distance_guard_calibration,
    retrieve_chunks,
)
from rag_core.question_normalization import normalize_question_for_exact_match
from rag_core.retrieval import RetrievedChunk, keyword_index_status, normalize_tenant_id
from rag_core.review_actions import (
    ALLOWED_ACTION_TYPES,
    ALLOWED_STATUS_AFTER,
    append_review_action_event,
    build_review_action_event,
)
from rag_core.source_metadata import normalize_citation
from rag_core.tenant_profile import resolve_tenant_product_profile
from rag_core.utils import ensure_openai_client
from webapi import branding
from webapi import conversation_store
from webapi import ingestion_jobs
from webapi import metrics_registry
from webapi import oidc_auth
from webapi.admin_auth import (
    ADMIN_CSRF_COOKIE,
    ADMIN_SESSION_COOKIE,
    admin_auth_enabled,
    admin_scope,
    csrf_token_for,
    enforce_admin_token,
    issue_admin_session,
    require_admin_auth,
    require_global,
    require_scope,
    revoke_admin_session,
    revoke_all_admin_sessions,
    token_scope,
)
from webapi.api_auth import (
    ApiAuthContext,
    _parse_tenant_map,
    api_auth_enabled,
    enforce_tenant_authorization,
    require_search_debug_access,
)
from webapi.rate_limit import FixedWindowRateLimiter, require_api_auth_rate_limited
from webapi.rbac import ROLE_OPERATOR, ROLE_USER, caller_has_role, enforce_role
from webapi import production_guard
from webapi import synonym_review
from webapi import security_middleware
from webapi import state_backend
from rag_core import vector_backend
from starlette.exceptions import HTTPException as StarletteHTTPException
from rag_core import conversation_context
from webapi.security_middleware import HardeningMiddleware
from rag_core.audit_log import set_audit_actor, write_failure_count
from rag_core.review_actions import latest_review_statuses


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
_start_time = time.time()
_total_requests = 0
_error_requests = 0
_approved_qa_index: ApprovedQAIndex | None = None
_approved_qa_index_path: str | None = None
_ALLOWED_FEEDBACK_TYPES = {
    "good",
    "bad",
    "neutral",
    "human_review_requested",
}
_REVIEW_ITEM_FIELDS = (
    "review_id",
    "priority",
    "status",
    "tenant_id",
    "user_query",
    "answer_mode",
    "confidence_route",
    "decision_route",
    "candidate_ids",
    "selected_candidate_id",
    "feedback_type",
    "bad_reason",
    "created_at",
    "source",
    "reasons",
)


def _int_env(name: str, default: int) -> int:
    try:
        value = int(str(os.getenv(name, "")).strip() or default)
    except ValueError:
        return default
    return value if value > 0 else default


# Input bounds (6-1). Over-long questions and huge top_k / context sizes are
# rejected with 422 before any retrieval or model call.
_MAX_QUESTION_CHARS = _int_env("MAX_QUESTION_CHARS", 4000)
_MAX_TOP_K = _int_env("MAX_TOP_K", 100)
_MAX_CONTEXT_CHARS = _int_env("MAX_CONTEXT_CHARS_LIMIT", 32000)


class ChatRequest(BaseModel):
    question: str = Field(..., max_length=_MAX_QUESTION_CHARS)
    top_k: Optional[int] = Field(None, ge=1, le=_MAX_TOP_K)
    max_context_chars: Optional[int] = Field(None, ge=1, le=_MAX_CONTEXT_CHARS)
    trace_id: Optional[str] = None
    tenant_id: Optional[str] = None
    staging_collection: Optional[str] = None
    rag_profile_id: Optional[str] = None
    # Conversation thread for follow-up completion (CONVERSATION_CONTEXT_ENABLED).
    thread_id: Optional[str] = Field(None, max_length=128)
    # "Look deeper": more candidates, multi-query on for this request, no answer cache.
    deep: bool = False


class SearchRequest(BaseModel):
    query: str = Field(..., max_length=_MAX_QUESTION_CHARS)
    top_k: Optional[int] = Field(None, ge=1, le=_MAX_TOP_K)
    trace_id: Optional[str] = None
    tenant_id: Optional[str] = None
    staging_collection: Optional[str] = None


class SearchDebugRequest(BaseModel):
    query: str = Field(..., max_length=_MAX_QUESTION_CHARS)
    top_k: Optional[int] = Field(None, ge=1, le=_MAX_TOP_K)
    max_context_chars: Optional[int] = Field(None, ge=1, le=_MAX_CONTEXT_CHARS)
    trace_id: Optional[str] = None
    include_context: bool = False
    generate_answer: bool = True
    include_approved_similar_candidates: bool = False
    approved_similar_top_k: Optional[int] = None
    tenant_id: Optional[str] = None
    staging_collection: Optional[str] = None


class ProductPreviewChatRequest(BaseModel):
    query: Optional[str] = Field(None, max_length=_MAX_QUESTION_CHARS)
    message: Optional[str] = Field(None, max_length=_MAX_QUESTION_CHARS)
    tenant_id: Optional[str] = "default"
    customer_id: Optional[str] = None
    top_k: Optional[int] = 3
    keyword_profile: Optional[str] = None
    threshold_profile: Optional[str] = None
    rerank_profile: Optional[str] = None
    apply_feedback_preview: Optional[bool] = False
    apply_feature_rerank: Optional[bool] = False
    feature_rerank_profile: Optional[str] = None
    product_profile: Optional[str] = None
    product_profile_overrides: Optional[Dict[str, Any]] = None
    use_tenant_profile: Optional[bool] = False


class ProductFeedbackRequest(BaseModel):
    feedback_token: str
    feedback_type: str
    request_id: Optional[str] = None
    trace_id: Optional[str] = None
    tenant_id: Optional[str] = "default"
    selected_candidate_id: Optional[str] = None
    shown_candidate_ids: Optional[List[str]] = None
    shown_rank: Optional[int] = None
    bad_reason: Optional[str] = None
    comment: Optional[str] = None


class ReviewActionRequest(BaseModel):
    review_id: str
    action_type: str
    feedback_token: Optional[str] = None
    tenant_id: Optional[str] = "default"
    selected_candidate_id: Optional[str] = None
    operator_note: Optional[str] = None
    status_after: Optional[str] = None
    tags: Optional[List[str]] = None


# Product-preview profiles are selected by name from these allowlists only;
# request values are never used as file paths.
_PRODUCT_PREVIEW_KEYWORD_PROFILES = {
    "default": "configs/approved_similar_keyword_weights.json",
}
_PRODUCT_PREVIEW_THRESHOLD_PROFILES = {
    "default": "configs/approved_similar_decision_thresholds.json",
    "balanced": "configs/approved_similar_decision_thresholds_balanced.json",
    "conservative": "configs/approved_similar_decision_thresholds_conservative.json",
}


def _product_preview_profile_path(name: str | None, allowed: Dict[str, str], field: str) -> str | None:
    value = str(name or "").strip()
    if not value:
        return None
    relative = allowed.get(value)
    if relative is None:
        raise HTTPException(status_code=400, detail=f"unknown {field}")
    return str(Path(config.BASE_DIR) / relative)


def _is_non_serving_product_profile(name: str | None) -> bool:
    value = str(name or "").strip()
    if not value:
        return False
    try:
        profile = load_product_profile(value)
    except Exception:
        return False
    return profile.get("profile_name") == value and not bool(profile.get("runtime_serving", False))


@contextmanager
def _temporary_product_preview_profiles(
    *,
    keyword_profile_path: str | None,
    threshold_profile_path: str | None,
):
    # Request-scoped (contextvars): os.environ is never modified, so concurrent
    # requests cannot observe each other's profile.
    with approved_similar.profile_overrides(
        keyword_profile_path=keyword_profile_path,
        decision_thresholds_path=threshold_profile_path,
    ):
        yield


def _preview(text: Any, max_chars: int) -> str:
    value = str(text or "")
    if len(value) <= max_chars:
        return value
    suffix = "...[truncated]"
    if max_chars <= len(suffix):
        return value[:max_chars]
    return value[: max_chars - len(suffix)] + suffix


def _compact_chunk(chunk: RetrievedChunk, *, max_preview_chars: int) -> Dict[str, Any]:
    meta = chunk.metadata or {}
    item: Dict[str, Any] = {
        "chunk_id": str(meta.get("id") or ""),
        "source_doc": str(meta.get("source_doc") or meta.get("doc") or "unknown"),
        "source_pages": meta.get("source_pages") or meta.get("pages") or [],
        "retrieval_source": meta.get("retrieval_source"),
        "score": float(chunk.score),
        "doc_type": meta.get("doc_type"),
        "title": meta.get("title"),
        "section_path": meta.get("section_path"),
        "chunk_role": meta.get("chunk_role"),
        "parent_chunk_id": meta.get("parent_chunk_id"),
        "text_preview": _preview(chunk.text, max_preview_chars),
    }
    if "score_details" in meta:
        item["score_details"] = meta.get("score_details")
    return item


def _compact_chunks(raw: Any, *, max_preview_chars: int) -> List[Dict[str, Any]]:
    if not isinstance(raw, (list, tuple)):
        return []
    return [
        _compact_chunk(chunk, max_preview_chars=max_preview_chars)
        for chunk in raw
        if isinstance(chunk, RetrievedChunk)
    ]


def _top_source_docs(chunks: Any, limit: int = 5) -> List[str]:
    out: List[str] = []
    if not isinstance(chunks, (list, tuple)):
        return out
    for chunk in chunks:
        if not isinstance(chunk, RetrievedChunk):
            continue
        meta = chunk.metadata or {}
        source_doc = str(meta.get("source_doc") or meta.get("doc") or "unknown")
        if source_doc not in out:
            out.append(source_doc)
        if len(out) >= limit:
            break
    return out


def _top_score_detail_summary(chunks: Any) -> Dict[str, Any]:
    if not isinstance(chunks, (list, tuple)):
        return {
            "top_keyword_score": None,
            "top_matched_terms": [],
            "top_matched_fields": [],
            "top_keyword_boost_applied": False,
            "top_keyword_boost_value": 0.0,
            "top_boost_reason": [],
        }
    for chunk in chunks:
        if not isinstance(chunk, RetrievedChunk):
            continue
        meta = chunk.metadata or {}
        details = meta.get("score_details")
        if not isinstance(details, dict):
            continue
        return {
            "top_keyword_score": details.get("keyword_score"),
            "top_matched_terms": list(details.get("matched_terms") or [])[:10],
            "top_matched_fields": list(details.get("matched_fields") or [])[:10],
            "top_keyword_boost_applied": bool(details.get("keyword_boost_applied")),
            "top_keyword_boost_value": details.get("keyword_boost_value"),
            "top_boost_reason": list(details.get("boost_reason") or [])[:8],
        }
    return {
        "top_keyword_score": None,
        "top_matched_terms": [],
        "top_matched_fields": [],
        "top_keyword_boost_applied": False,
        "top_keyword_boost_value": 0.0,
        "top_boost_reason": [],
    }


def _trace_value(trace: Dict[str, Any], key: str, default: Any = None) -> Any:
    value = trace.get(key)
    return default if value is None else value


def _embedding_client():
    if embedding_provider.is_local_provider():
        return None
    return ensure_openai_client(base_url=config.OPENAI_BASE_URL)


def _generation_error_payload(exc: Exception) -> tuple[int, Dict[str, str]] | None:
    status_code = getattr(exc, "status_code", None)
    code = str(getattr(exc, "code", "") or "").lower()
    message = str(exc).lower()
    if "insufficient_quota" in code or "insufficient_quota" in message:
        return 429, {
            "detail": "chat generation unavailable",
            "error_type": "insufficient_quota",
        }
    if status_code == 429 or "rate limit" in message or "rate_limit" in code:
        return 429, {
            "detail": "chat generation unavailable",
            "error_type": "rate_limited",
        }
    if status_code in {500, 502, 503, 504}:
        return 503, {
            "detail": "chat generation unavailable",
            "error_type": "provider_unavailable",
        }
    return None


def _approved_qa_lookup(question: str, tenant_id: str = "default") -> ApprovedAnswer | None:
    index = _approved_qa_index_for(tenant_id)
    if index is None:
        return None
    return lookup_approved_answer(index, question, tenant_id=normalize_tenant_id(tenant_id))


def _approved_qa_index_for(tenant_id: str = "default"):
    """The approved-QA index of a tenant (cached), or None when approved QA is off."""
    global _approved_qa_index, _approved_qa_index_path
    if not getattr(config, "APPROVED_QA_ENABLED", False):
        return None
    tenant = normalize_tenant_id(tenant_id)
    path = str(config.APPROVED_QA_PATH)
    # Single-slot cache keyed by path AND tenant: load_approved_qa filters
    # records per tenant, so an index loaded for one tenant must never serve
    # another.
    # The file's mtime is part of the key: an edited approved-QA file is
    # reloaded on the next request instead of after a restart.
    try:
        mtime_ns = os.stat(path).st_mtime_ns
    except OSError:
        mtime_ns = None
    cache_key = f"{path}::{tenant}::{mtime_ns}"
    if _approved_qa_index is None or _approved_qa_index_path != cache_key:
        _approved_qa_index = load_approved_qa(path, tenant_id=tenant)
        _approved_qa_index_path = cache_key
    return _approved_qa_index


def _approved_chat_payload(answer: ApprovedAnswer) -> Dict[str, Any]:
    citations = []
    for idx, citation in enumerate(answer.approved_citations, start=1):
        payload = normalize_citation(
            {
                "source_doc": citation.source_doc,
                "source_pages": list(citation.source_pages),
                "chunk_id": citation.chunk_id,
                "title": citation.title,
                "source_id": citation.source_id,
                "source_title": citation.source_title,
                "source_type": citation.source_type,
                "version": citation.version,
                "status": citation.status,
                "updated_at": citation.updated_at,
                "tenant_id": citation.tenant_id,
            }
        )
        payload["number"] = idx
        payload.setdefault("source_doc", citation.source_doc)
        payload.setdefault("source_pages", list(citation.source_pages))
        payload.setdefault("chunk_id", citation.chunk_id)
        payload.setdefault("title", citation.title)
        citations.append(payload)
    answer_mode = (
        "approved_alias_match" if answer.match_type == "alias" else "approved_exact_match"
    )
    return {
        "answer_text": answer.approved_answer,
        "answer_with_footnotes": answer.approved_answer,
        "intent": answer_mode,
        "guard_reason": None,
        "used_fallback": False,
        "citations": citations,
        "retrieved": [],
        "rewritten_query": "",
        "augmented_query": "",
        "answer_mode": answer_mode,
        "approved_qa_id": answer.qa_id,
        "normalized_question": answer.normalized_question,
        "canonical_approved_question": answer.question,
        "matched_alias": answer.matched_alias,
        "retrieval_source": "approved_qa_alias" if answer.match_type == "alias" else "approved_qa_exact",
        "retrieval_required": False,
        "llm_used": False,
    }


def _approved_answer_mode(answer: ApprovedAnswer) -> str:
    return "approved_alias_match" if answer.match_type == "alias" else "approved_exact_match"


def _approved_audit_fields(answer: ApprovedAnswer, input_question: str) -> Dict[str, Any]:
    return {
        "normalized_question": answer.normalized_question,
        "normalized_input_question": normalize_question_for_exact_match(input_question),
        "answer_mode": _approved_answer_mode(answer),
        "approved_qa_id": answer.qa_id,
        "matched_alias": answer.matched_alias,
        "canonical_question": answer.question,
        "citations_count": len(answer.approved_citations),
        "retrieval_required": False,
        "llm_used": False,
    }


def _approved_exact_hit_to_chat_payload(hit: dict, question: str, tenant_id: str) -> Dict[str, Any]:
    meta = dict(hit.get("metadata") or {})
    approved_answer = str(
        meta.get("approved_answer")
        or meta.get("answer_text")
        or hit.get("answer_text")
        or ""
    )
    raw_citations = meta.get("approved_citations")
    citations = []
    if isinstance(raw_citations, list) and raw_citations:
        for idx, citation in enumerate(raw_citations, start=1):
            if not isinstance(citation, dict):
                continue
            payload = normalize_citation(citation)
            payload["number"] = idx
            citations.append(payload)
    else:
        source_pages = meta.get("source_pages")
        if not source_pages:
            page_start = meta.get("source_page_start")
            page_end = meta.get("source_page_end")
            if page_start and page_end and page_start != page_end:
                source_pages = [page_start, page_end]
            elif page_start:
                source_pages = [page_start]
        citation = normalize_citation(
            {
                "source_doc": meta.get("source_doc") or meta.get("doc_id") or "",
                "source_pages": source_pages or [],
                "chunk_id": meta.get("chunk_id") or meta.get("id") or hit.get("id"),
                "title": meta.get("title"),
                "tenant_id": tenant_id,
            }
        )
        citation["number"] = 1
        citations.append(citation)

    qa_id = str(meta.get("qa_id") or meta.get("approved_qa_id") or meta.get("id") or hit.get("id") or "")
    normalized_question = str(meta.get("normalized_question") or meta.get("question_text") or question)
    return {
        "answer": approved_answer,
        "answer_text": approved_answer,
        "answer_with_footnotes": approved_answer,
        "intent": "approved_exact_match",
        "guard_reason": None,
        "used_fallback": False,
        "abstained": False,
        "citations": citations,
        "retrieved": [],
        "rewritten_query": "",
        "augmented_query": "",
        "answer_mode": "approved_exact_match",
        "approved_qa_id": qa_id,
        "normalized_question": normalized_question,
        "retrieval_source": "approved_qa_exact",
        "tenant_id": tenant_id,
    }


def _approved_exact_chat_lookup(question: str, tenant_id: str) -> Optional[Dict[str, Any]]:
    if not getattr(config, "APPROVED_QA_ENABLED", False):
        return None
    hits = lookup_approved_qa_exact(question, tenant_id=tenant_id, limit=1)
    if not hits:
        return None
    return _approved_exact_hit_to_chat_payload(hits[0], question, tenant_id)


def _approved_product_citations(answer: ApprovedAnswer) -> List[Dict[str, Any]]:
    return _approved_chat_payload(answer)["citations"]


def _product_preview_decision_metadata(
    *,
    route: str,
    candidate_count: int,
    top_k: int,
    keyword_profile: str | None,
    threshold_profile: str | None,
    exact_match_checked: bool,
    auto_answer_suppressed_for_similar_candidates: bool,
    audit_event: Dict[str, Any],
    decision: Dict[str, Any] | None = None,
    feedback_preview: Dict[str, Any] | None = None,
    feature_rerank: Dict[str, Any] | None = None,
    product_policy: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    payload = {
        "route": route,
        "candidate_count": candidate_count,
        "top_k": top_k,
        "keyword_profile": keyword_profile,
        "threshold_profile": threshold_profile,
        "auto_answer_suppressed_for_similar_candidates": auto_answer_suppressed_for_similar_candidates,
        "exact_match_checked": exact_match_checked,
        "decision_gate": dict(decision or {}),
        "audit_event_preview": audit_event,
    }
    if feedback_preview:
        payload.update(dict(feedback_preview))
    if feature_rerank:
        payload.update(dict(feature_rerank))
    if product_policy:
        payload.update(dict(product_policy))
    return payload


def _feedback_preview_request_metadata(
    *,
    apply_feedback_preview: bool,
    rerank_profile: str | None,
) -> Dict[str, Any]:
    return {
        "apply_feedback_preview": bool(apply_feedback_preview),
        "rerank_profile": rerank_profile,
        "feedback_preview_applied": False,
        "feedback_preview_profile_path": str(PROFILE_PATH),
        "feedback_preview_adjusted_candidate_count": 0,
        "feedback_preview_missing_profile": False,
        "feedback_preview_invalid_profile": False,
        "feedback_preview_safety_checked": False,
        "feedback_preview_reordered": False,
    }


def _product_policy_context(
    *,
    product_profile: str | None,
    product_profile_overrides: Dict[str, Any] | None,
    requested_top_k: int,
    requested_feedback_preview: bool,
    requested_feature_rerank: bool,
    use_tenant_profile: bool = False,
    tenant_id: str | None = None,
    customer_id: str | None = None,
) -> Dict[str, Any]:
    tenant_resolution: Dict[str, Any] | None = None
    effective_product_profile = product_profile
    if use_tenant_profile:
        tenant_resolution = resolve_tenant_product_profile(
            tenant_id or "default",
            customer_id=customer_id,
            requested_profile=product_profile,
            strict=False,
        )
        effective_product_profile = tenant_resolution.get("resolved_profile") or tenant_resolution.get("default_profile")

    if not effective_product_profile:
        return {
            "policy": None,
            "metadata": None,
            "warnings": [],
            "effective_top_k": requested_top_k,
            "display_top_k": requested_top_k,
            "effective_apply_feedback_preview": requested_feedback_preview,
            "effective_apply_feature_rerank": requested_feature_rerank,
            "tenant_resolution": tenant_resolution,
            "tenant_blocked": False,
        }

    warnings: List[str] = []
    if tenant_resolution:
        warnings.extend(str(warning) for warning in tenant_resolution.get("warnings") or [])
        if tenant_resolution.get("decision") in {"disabled", "rejected"}:
            metadata = {
                "use_tenant_profile": True,
                "tenant_profile_resolution": tenant_resolution,
                "product_profile": None,
                "requested_product_profile": str(product_profile or "").strip(),
                "effective_apply_feedback_preview": False,
                "effective_apply_feature_rerank": False,
                "enabled_steps": [],
                "policy_warnings": warnings,
            }
            return {
                "policy": None,
                "metadata": metadata,
                "warnings": warnings,
                "effective_top_k": requested_top_k,
                "display_top_k": requested_top_k,
                "effective_apply_feedback_preview": False,
                "effective_apply_feature_rerank": False,
                "tenant_resolution": tenant_resolution,
                "tenant_blocked": True,
            }

    requested_name = str(effective_product_profile or "").strip()
    try:
        profile = load_product_profile(requested_name)
    except Exception:
        fallback_name = "production_safe" if use_tenant_profile else "default"
        profile = load_product_profile(fallback_name)
        warnings.append("product_profile_load_failed_fallback_default")
    if requested_name and profile.get("profile_name") != requested_name:
        warnings.append("product_profile_fallback_default")

    overrides = product_profile_overrides if isinstance(product_profile_overrides, dict) else None
    if product_profile_overrides is not None and overrides is None:
        warnings.append("invalid_product_profile_overrides_ignored")
    policy = build_route_policy(profile, overrides)
    warnings.extend(str(warning) for warning in policy.get("warnings") or [])
    enabled = set(policy.get("enabled_steps") or [])
    limits = policy.get("limits") if isinstance(policy.get("limits"), dict) else {}
    max_internal = limits.get("max_candidates_internal")
    max_display = limits.get("max_candidates_display")
    effective_top_k = requested_top_k
    display_top_k = requested_top_k
    if isinstance(max_internal, int) and max_internal > 0:
        effective_top_k = max(1, min(effective_top_k, max_internal))
    if isinstance(max_display, int) and max_display > 0:
        display_top_k = max(1, min(display_top_k, max_display, effective_top_k))

    effective_feedback = bool(requested_feedback_preview and "feedback_preview_rerank" in enabled)
    effective_feature = bool(requested_feature_rerank and "feature_rerank" in enabled)
    if requested_feedback_preview and not effective_feedback:
        warnings.append("feedback_preview_rerank_blocked_by_product_policy")
    if requested_feature_rerank and not effective_feature:
        warnings.append("feature_rerank_blocked_by_product_policy")
    if requested_feedback_preview and requested_feature_rerank and "combined_rerank" not in enabled:
        warnings.append("combined_rerank_not_enabled_by_product_policy")

    metadata = {
        "product_profile": policy.get("profile_name"),
        "requested_product_profile": str(product_profile or "").strip() if use_tenant_profile else requested_name,
        "operation_mode": policy.get("operation_mode"),
        "runtime_serving": policy.get("runtime_serving"),
        "enabled_steps": list(policy.get("enabled_steps") or []),
        "policy_warnings": warnings,
        "effective_apply_feedback_preview": effective_feedback,
        "effective_apply_feature_rerank": effective_feature,
        "max_candidates_display": display_top_k,
    }
    if tenant_resolution:
        metadata.update(
            {
                "use_tenant_profile": True,
                "tenant_profile_resolution": tenant_resolution,
            }
        )
    return {
        "policy": policy,
        "metadata": metadata,
        "warnings": warnings,
        "effective_top_k": effective_top_k,
        "display_top_k": display_top_k,
        "effective_apply_feedback_preview": effective_feedback,
        "effective_apply_feature_rerank": effective_feature,
        "tenant_resolution": tenant_resolution,
        "tenant_blocked": False,
    }


def _feature_rerank_request_metadata(
    *,
    apply_feature_rerank: bool,
    feature_rerank_profile: str | None,
) -> Dict[str, Any]:
    return {
        "apply_feature_rerank": bool(apply_feature_rerank),
        "feature_rerank_profile": feature_rerank_profile,
        "feature_rerank_applied": False,
        "feature_rerank_candidate_count": 0,
        "feature_rerank_adjusted_candidate_count": 0,
        "feature_rerank_negative_mismatch_count": 0,
        "feature_rerank_reordered": False,
        "feature_rerank_profile_valid": False,
        "feature_rerank_missing_profile": False,
        "feature_rerank_safety_checked": False,
    }


def _apply_feature_rerank_preview(
    query: str,
    candidates: List[Dict[str, Any]],
    *,
    apply_feature_rerank: bool,
    feature_rerank_profile: str | None,
) -> tuple[List[Dict[str, Any]], Dict[str, Any], List[str]]:
    meta = _feature_rerank_request_metadata(
        apply_feature_rerank=apply_feature_rerank,
        feature_rerank_profile=feature_rerank_profile,
    )
    if not apply_feature_rerank:
        return [dict(candidate or {}) for candidate in candidates], meta, []
    if feature_rerank_profile not in {None, "approved_similar_feature_preview"}:
        meta["feature_rerank_profile_valid"] = False
        meta["feature_rerank_safety_checked"] = True
        return [dict(candidate or {}) for candidate in candidates], meta, ["feature_rerank_profile_ignored"]

    profile_path = approved_similar_feature_reranker.DEFAULT_PROFILE_PATH
    if not profile_path.exists():
        meta["feature_rerank_missing_profile"] = True
        return [dict(candidate or {}) for candidate in candidates], meta, ["feature_rerank_profile_missing"]

    try:
        profile = approved_similar_feature_reranker.load_feature_rerank_profile(profile_path)
        reranked, summary = approved_similar_feature_reranker.apply_feature_rerank(
            query,
            candidates,
            profile=profile,
        )
    except Exception:
        meta["feature_rerank_safety_checked"] = True
        return [dict(candidate or {}) for candidate in candidates], meta, ["feature_rerank_profile_invalid"]

    meta.update(
        {
            "feature_rerank_profile": feature_rerank_profile or "approved_similar_feature_preview",
            "feature_rerank_applied": bool(summary.get("feature_rerank_applied")),
            "feature_rerank_candidate_count": int(summary.get("candidate_count") or 0),
            "feature_rerank_adjusted_candidate_count": int(summary.get("adjusted_candidate_count") or 0),
            "feature_rerank_negative_mismatch_count": int(summary.get("negative_mismatch_count") or 0),
            "feature_rerank_reordered": bool(summary.get("reordered")),
            "feature_rerank_profile_valid": bool(summary.get("valid_profile")),
            "feature_rerank_missing_profile": False,
            "feature_rerank_safety_checked": True,
        }
    )
    if not summary.get("valid_profile"):
        return [dict(candidate or {}) for candidate in candidates], meta, ["feature_rerank_profile_invalid"]
    return reranked, meta, ["feature_rerank_applied_preview_only"]


def _product_preview_audit_payload(
    audit_event: Dict[str, Any],
    *,
    candidate_count: int,
    top_k: int,
    auto_answer_suppressed_for_similar_candidates: bool,
    exact_match_checked: bool,
    feedback_preview: Dict[str, Any] | None = None,
    feature_rerank: Dict[str, Any] | None = None,
    product_policy: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    payload = dict(audit_event or {})
    payload.update(
        {
            "candidate_count": candidate_count,
            "top_k": top_k,
            "auto_answer_suppressed_for_similar_candidates": auto_answer_suppressed_for_similar_candidates,
            "exact_match_checked": exact_match_checked,
        }
    )
    if feedback_preview:
        for key in (
            "apply_feedback_preview",
            "rerank_profile",
            "feedback_preview_applied",
            "feedback_preview_adjusted_candidate_count",
            "feedback_preview_reordered",
        ):
            payload[key] = feedback_preview.get(key)
    if feature_rerank:
        for key in (
            "apply_feature_rerank",
            "feature_rerank_profile",
            "feature_rerank_applied",
            "feature_rerank_adjusted_candidate_count",
            "feature_rerank_negative_mismatch_count",
            "feature_rerank_reordered",
        ):
            payload[key] = feature_rerank.get(key)
    if product_policy:
        for key in (
            "product_profile",
            "operation_mode",
            "enabled_steps",
            "effective_apply_feedback_preview",
            "effective_apply_feature_rerank",
        ):
            payload[key] = product_policy.get(key)
        if product_policy.get("use_tenant_profile"):
            resolution = product_policy.get("tenant_profile_resolution")
            if isinstance(resolution, dict):
                payload["use_tenant_profile"] = True
                payload["resolved_profile"] = resolution.get("resolved_profile")
                payload["requested_profile"] = resolution.get("requested_profile")
                payload["tenant_status"] = resolution.get("tenant_status")
                payload["tenant_profile_decision"] = resolution.get("decision")
        payload["policy_warnings"] = list(product_policy.get("policy_warnings") or [])[:12]
        payload["policy_warnings_count"] = len(product_policy.get("policy_warnings") or [])
    return payload


def _append_product_preview_audit(decision: Dict[str, Any], audit_payload: Dict[str, Any]) -> List[str]:
    if append_product_preview_chat_audit_event(audit_payload):
        decision["audit_persisted"] = True
        return []
    decision["audit_persisted"] = False
    return ["product_preview_audit_logging_failed"]


def _product_profile_info_metadata(product_policy_meta: Dict[str, Any] | None) -> Dict[str, Any]:
    if not product_policy_meta:
        return {}
    out = {
        "product_profile": product_policy_meta.get("product_profile"),
        "operation_mode": product_policy_meta.get("operation_mode"),
        "runtime_serving": product_policy_meta.get("runtime_serving"),
        "enabled_steps": product_policy_meta.get("enabled_steps"),
        "effective_apply_feedback_preview": product_policy_meta.get("effective_apply_feedback_preview"),
        "effective_apply_feature_rerank": product_policy_meta.get("effective_apply_feature_rerank"),
    }
    if product_policy_meta.get("use_tenant_profile"):
        out.update(
            {
                "use_tenant_profile": True,
                "tenant_profile_resolution": product_policy_meta.get("tenant_profile_resolution"),
                "policy_warnings": product_policy_meta.get("policy_warnings"),
            }
        )
    return out


def _bounded_text(value: Any, max_chars: int = 1000) -> str | None:
    if value is None:
        return None
    return _preview(str(value), max_chars)


def _bounded_id_list(values: Any, limit: int = 20) -> List[str]:
    if not isinstance(values, list):
        return []
    out: List[str] = []
    for value in values[:limit]:
        text = _bounded_text(value, 1000)
        if text:
            out.append(text)
    return out


def _feedback_audit_event(
    req: ProductFeedbackRequest,
    *,
    feedback_token: str | None = None,
    feedback_type: str | None = None,
) -> Dict[str, Any]:
    normalized_feedback_token = (
        feedback_token if feedback_token is not None else req.feedback_token
    )
    return {
        "feedback_token": _bounded_text(normalized_feedback_token, 1000),
        "feedback_type": feedback_type if feedback_type is not None else req.feedback_type,
        "request_id": _bounded_text(req.request_id, 1000),
        "trace_id": _bounded_text(req.trace_id, 1000),
        "tenant_id": _bounded_text(req.tenant_id or "default", 1000) or "default",
        "selected_candidate_id": _bounded_text(req.selected_candidate_id, 1000),
        "shown_candidate_ids": _bounded_id_list(req.shown_candidate_ids),
        "shown_rank": req.shown_rank,
        "bad_reason": _bounded_text(req.bad_reason, 1000),
        "comment": _bounded_text(req.comment, 1000),
    }


def _review_queue_path() -> Path:
    return Path(config.RUNS_DIR) / "review" / "review_queue.jsonl"


def _bounded_review_item(record: Dict[str, Any]) -> Dict[str, Any]:
    item: Dict[str, Any] = {}
    for field in _REVIEW_ITEM_FIELDS:
        value = record.get(field)
        if field == "candidate_ids":
            item[field] = _bounded_id_list(value, limit=20)
        elif field == "reasons":
            item[field] = [
                bounded
                for raw in (value if isinstance(value, list) else [])
                if (bounded := _bounded_text(raw, 300))
            ][:8]
        elif field == "user_query":
            item[field] = _bounded_text(value, 500)
        elif field == "bad_reason":
            item[field] = _bounded_text(value, 300)
        else:
            item[field] = _bounded_text(value, 1000)
    if not item.get("tenant_id"):
        item["tenant_id"] = "default"
    if not item.get("status"):
        item["status"] = "open"
    return item


def _load_review_items(
    *,
    status: str | None = None,
    priority: str | None = None,
    tenant_id: str | None = None,
    limit: int = 100,
    tenant_scope=None,
) -> Dict[str, Any]:
    source_path = _review_queue_path()
    safe_limit = max(0, min(int(limit or 100), 500))
    filters = {
        "status": status,
        "priority": priority,
        "tenant_id": tenant_id,
        "limit": safe_limit,
    }
    total_loaded = 0
    skipped_malformed = 0
    items: List[Dict[str, Any]] = []
    # Latest operator decision per (tenant, review_id) overrides the status
    # written when the queue was built.
    action_statuses = latest_review_statuses(Path(config.RUNS_DIR) / "review" / "review_actions.jsonl")

    if source_path.exists():
        with source_path.open("r", encoding="utf-8") as f:
            for line in f:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    record = json.loads(raw)
                except json.JSONDecodeError:
                    skipped_malformed += 1
                    continue
                if not isinstance(record, dict):
                    skipped_malformed += 1
                    continue
                total_loaded += 1
                item = _bounded_review_item(record)
                decided = action_statuses.get((str(item.get("tenant_id") or "default"), str(item.get("review_id") or "")))
                if decided:
                    item["status"] = decided
                if status and item.get("status") != status:
                    continue
                if priority and item.get("priority") != priority:
                    continue
                if tenant_id and item.get("tenant_id") != tenant_id:
                    continue
                if tenant_scope is not None and str(item.get("tenant_id") or "default") not in tenant_scope:
                    continue  # stage 15: a tenant administrator sees only its tenants
                if len(items) < safe_limit:
                    items.append(item)

    return {
        "items": items,
        "total_loaded": total_loaded,
        "returned_count": len(items),
        "skipped_malformed_lines": skipped_malformed,
        "source_path": str(source_path),
        "filters": filters,
    }


def _health_details() -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "status": "ok",
        "chroma_collection": config.resolve_chroma_collection_name(),
        "vectorstore_collection_name": config.VECTORSTORE_COLLECTION_NAME,
        "vectorstore_dir": config.VECTORSTORE_DIR,
        "chat_generation_mode": config.resolve_chat_generation_mode(),
    }
    from rag_core import index_generation

    active_generation = index_generation.active_pointer()
    if active_generation:
        payload["active_generation"] = {
            "collection": active_generation.get("collection"),
            "generation_id": active_generation.get("generation_id"),
            "activated_at": active_generation.get("activated_at"),
        }
    try:
        payload["distance_guard"] = distance_guard_calibration()
    except Exception:  # noqa: BLE001
        logging.exception("distance guard calibration status unavailable")
    try:
        payload.update(keyword_index_status())
    except Exception:
        logging.exception("keyword index status unavailable")
        payload.update(
            {
                "keyword_index_loaded": False,
                "keyword_index_records": 0,
                "keyword_index_path": str(config.CHUNKS_JSONL_PATH),
            }
        )
    try:
        payload.update(embedding_provider.active_fingerprint())
    except Exception:
        logging.exception("embedding fingerprint unavailable")
        payload.update({"embed_provider": None, "embed_model": None})
    return payload


@app.get("/health")
def health():
    # Production: liveness only. Paths, collection names and models are on
    # the admin-only /admin/health.
    if production_guard.is_production():
        return {"status": "ok"}
    return _health_details()


@app.get("/admin/health")
def admin_health(_admin_auth: None = Depends(require_admin_auth)):
    payload = _health_details()
    # Reachability of Redis / Postgres / Qdrant when they are configured (stage 10).
    payload["dependencies"] = {**state_backend.health(), **vector_backend.health()}
    return payload


@app.get("/ready")
def ready():
    # Readiness for a load balancer / orchestrator: 503 while a configured
    # shared service (Redis, Postgres, Qdrant) is unreachable. Names only.
    deps = {**state_backend.health(), **vector_backend.health()}
    failing = sorted(name for name, state in deps.items() if not state.get("ok"))
    if failing:
        return JSONResponse(status_code=503, content={"status": "not_ready", "failing": failing})
    return {"status": "ready"}


def require_metrics_access(request: Request) -> None:
    # Production: /metrics needs "Authorization: Bearer <METRICS_TOKEN>".
    if not production_guard.is_production():
        return
    import hmac

    expected = str(os.getenv("METRICS_TOKEN", "")).strip()
    header = str(request.headers.get("authorization") or "")
    provided = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not expected:
        raise HTTPException(status_code=503, detail="metrics token is not configured")
    if not provided:
        raise HTTPException(status_code=401, detail="metrics authentication required")
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="invalid metrics credentials")


def _record_chat_outcome_metrics(
    *,
    answer_mode: Optional[str],
    guard_reason: Optional[str] = None,
    used_fallback: bool = False,
) -> None:
    if answer_mode:
        metrics_registry.increment("chat_answer_mode_total", str(answer_mode))
    if guard_reason:
        metrics_registry.increment("chat_guard_reason_total", str(guard_reason))
    if used_fallback:
        metrics_registry.increment("chat_used_fallback_total")


def _record_provider_error_metric(generation_error) -> None:
    if generation_error:
        metrics_registry.increment(
            "chat_provider_error_total",
            str(generation_error[1].get("error_type") or "unknown"),
        )


@app.get("/metrics")
def metrics(format: Optional[str] = None, _metrics_auth: None = Depends(require_metrics_access)):
    # Counters are per-process; with multiple workers each process reports
    # its own numbers. Default output is JSON; ?format=prometheus returns the
    # Prometheus text exposition format generated from the same counters.
    payload = {
        "uptime_seconds": int(time.time() - _start_time),
        "total_requests": _total_requests,
        "error_requests": _error_requests,
        "counters": metrics_registry.aggregate_snapshot(),
        "counters_scope": metrics_registry.scope(),
    }
    if metrics_registry.scope() == "all_workers":
        totals = metrics_registry.aggregate_totals()
        payload["total_requests"] = totals["requests"]
        payload["error_requests"] = totals["errors"]
    audit_failures = write_failure_count()
    if audit_failures:
        # Audit writes that failed (disk full, permissions): alert on > 0.
        payload["counters"] = dict(payload["counters"])
        payload["counters"]["audit_write_failures_total"] = {"total": audit_failures}
    if str(format or "").strip().lower() == "prometheus":
        return PlainTextResponse(
            metrics_registry.to_prometheus(payload),
            media_type=metrics_registry.PROMETHEUS_CONTENT_TYPE,
        )
    return payload


@app.get("/product-preview", response_class=HTMLResponse)
def product_preview_page():
    path = _STATIC_DIR / "product_preview.html"
    try:
        return HTMLResponse(path.read_text(encoding="utf-8"))
    except Exception:
        logging.exception("product preview page unavailable")
        raise HTTPException(status_code=500, detail="product preview page unavailable")


# Scripts shared by the pages (stage 13; unit-tested with node --test).
_STATIC_SCRIPTS = {"render.js", "login.js"}


@app.get("/static/js/{name}")
def static_script(name: str):
    if name not in _STATIC_SCRIPTS:
        raise HTTPException(status_code=404, detail="not found")
    return PlainTextResponse(
        (_STATIC_DIR / "js" / name).read_text(encoding="utf-8"),
        media_type="application/javascript; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/chat-ui", response_class=HTMLResponse)
def chat_ui_page():
    # Serves the minimal end-user chat UI (static shell only). The page calls
    # the existing /chat/stream and /chat/feedback endpoints, which keep their
    # own API-auth / tenant-authorization / rate-limit enforcement; this route
    # adds no new auth scheme and carries no secrets.
    path = _STATIC_DIR / "chat.html"
    try:
        return HTMLResponse(path.read_text(encoding="utf-8"))
    except Exception:
        logging.exception("chat ui page unavailable")
        raise HTTPException(status_code=500, detail="chat ui page unavailable")


def _require_oidc() -> None:
    if not oidc_auth.oidc_enabled():
        raise HTTPException(status_code=404, detail="not found")


@app.get("/auth/oidc/login")
def oidc_login():
    # Default-off (404 when disabled). Builds the Authorization Code + PKCE
    # redirect and binds state/nonce/verifier in a signed, httponly txn cookie.
    _require_oidc()
    auth_url, txn = oidc_auth.build_login()
    resp = RedirectResponse(url=auth_url, status_code=302)
    resp.set_cookie(
        oidc_auth.TXN_COOKIE, txn, max_age=600, httponly=True,
        samesite="lax", secure=oidc_auth.cookie_secure(), path="/auth/oidc",
    )
    return resp


@app.get("/auth/oidc/callback")
def oidc_callback(request: Request, code: Optional[str] = None, state: Optional[str] = None):
    _require_oidc()
    if not code or not state:
        raise HTTPException(status_code=400, detail="missing code/state")
    txn_cookie = request.cookies.get(oidc_auth.TXN_COOKIE) or ""
    session = oidc_auth.complete_login(code=code, state=state, txn_cookie=txn_cookie)
    resp = RedirectResponse(url="/chat-ui", status_code=302)
    resp.set_cookie(
        oidc_auth.SESSION_COOKIE, session, max_age=3600, httponly=True,
        samesite="lax", secure=oidc_auth.cookie_secure(), path="/",
    )
    resp.delete_cookie(oidc_auth.TXN_COOKIE, path="/auth/oidc")
    return resp


@app.get("/auth/oidc/logout")
@app.post("/auth/oidc/logout")
def oidc_logout(request: Request):
    _require_oidc()
    # Revoke server-side too: a copied cookie stops working after logout.
    oidc_auth.revoke_session_from_headers(request.headers)
    # end_session_url (when the IdP publishes one): the UI sends the browser
    # there so the IdP session ends too (RP-initiated logout).
    resp = JSONResponse({"ok": True, **oidc_auth.end_session_info()})
    resp.delete_cookie(oidc_auth.SESSION_COOKIE, path="/")
    return resp


@app.get("/branding")
def branding_endpoint():
    # Safe, secret-free display config for the workspace (logo text, product
    # name, subtitle, theme color). No API keys, tokens, or tenant data.
    return branding.branding_config()


@app.get("/ui/context")
def ui_context(request: Request):
    # Backend-authoritative UI context. Role is used by the UI only to show/hide
    # panels; privileged routes (/admin/review*) are independently enforced by
    # require_admin_auth. No secrets / raw keys are returned.
    # Prefer an authenticated OIDC session role (Prompt047) when present;
    # otherwise fall back to the admin-token-based role. Backend-authoritative.
    oidc_ctx = oidc_auth.resolve_oidc_session(request.headers)
    role = (oidc_ctx.role if (oidc_ctx and oidc_ctx.role) else None) or branding.resolve_role(request.headers)
    return {
        "role": role,
        "admin_auth_enabled": admin_auth_enabled(),
        "api_auth_enabled": api_auth_enabled(),
    }


# --- Admin browser login (stage 9) -------------------------------------------
# Admin auth is fail-closed and browsers cannot send X-Admin-Token on a page
# navigation, so the admin pages were unreachable from a browser. /admin/login
# exchanges the token for an httponly, SameSite=Strict session cookie (signed
# with the token; ADMIN_SESSION_TTL_SECONDS, default 8h).


class AdminLoginRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=512)


_ADMIN_LOGIN_LIMITER = FixedWindowRateLimiter()


def _admin_login_attempts_per_minute() -> int:
    try:
        return max(1, int(os.getenv("ADMIN_LOGIN_ATTEMPTS_PER_MINUTE", "") or 10))
    except ValueError:
        return 10


def _request_scope(request: Optional[Request]):
    # Direct function calls (tests) bypass FastAPI and its admin dependency,
    # like enforce_tenant_authorization; served requests always carry one.
    return admin_scope(request.headers) if isinstance(request, Request) else None


def _admin_actor(scope) -> Dict[str, Any]:
    # Audit actor of an admin request: never the token, only its reach.
    return {"admin_scope": "installation" if scope is None else sorted(scope)}


def _audit_admin(scope, action: str, **fields: Any) -> None:
    """Stage 15: administrative changes are written to the audit log."""
    append_audit_event("admin", {"action": action, **_admin_actor(scope), **fields})


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page():
    path = _STATIC_DIR / "admin_login.html"
    try:
        return HTMLResponse(path.read_text(encoding="utf-8"))
    except Exception:
        logging.exception("admin login page unavailable")
        raise HTTPException(status_code=500, detail="admin login page unavailable")


@app.post("/admin/login")
def admin_login(req: AdminLoginRequest, request: Request):
    if not admin_auth_enabled():
        raise HTTPException(status_code=404, detail="not found")
    # Always limited, independent of RATE_LIMIT_ENABLED: one bucket per address.
    host = request.client.host if request.client else "unknown"
    bucket = "admin-login:" + hashlib.sha256(host.encode("utf-8")).hexdigest()[:12]
    allowed, retry_after = _ADMIN_LOGIN_LIMITER.check(bucket, _admin_login_attempts_per_minute())
    if not allowed:
        raise HTTPException(
            status_code=429, detail="too many login attempts", headers={"Retry-After": str(max(1, int(retry_after) + 1))}
        )
    try:
        enforce_admin_token({"x-admin-token": req.token})
    except HTTPException:
        metrics_registry.increment("api_auth_rejection_total", "admin_login_failed")
        _audit_admin(frozenset(), "admin_login_failed")
        raise
    scope = token_scope(req.token)[1]
    cookie, ttl = issue_admin_session(scope=scope)
    _audit_admin(scope, "admin_login")
    resp = JSONResponse({"ok": True, "expires_in": ttl})
    secure = oidc_auth.cookie_secure()
    resp.set_cookie(
        ADMIN_SESSION_COOKIE, cookie, max_age=ttl, httponly=True,
        samesite="strict", secure=secure, path="/",
    )
    # Readable by the admin pages' script, which echoes it in X-CSRF-Token.
    resp.set_cookie(
        ADMIN_CSRF_COOKIE, csrf_token_for(cookie), max_age=ttl, httponly=False,
        samesite="strict", secure=secure, path="/",
    )
    return resp


@app.post("/admin/logout")
def admin_logout(request: Request):
    # The session is revoked on the server too: a copied cookie stops working.
    revoke_admin_session(request.cookies.get(ADMIN_SESSION_COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(ADMIN_SESSION_COOKIE, path="/")
    resp.delete_cookie(ADMIN_CSRF_COOKIE, path="/")
    return resp


@app.post("/admin/logout-all")
def admin_logout_all(request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    # Ends every admin browser session issued until now, on every node.
    scope = _request_scope(request)
    require_global(scope)
    revoke_all_admin_sessions()
    _audit_admin(scope, "admin_logout_all")
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(ADMIN_SESSION_COOKIE, path="/")
    resp.delete_cookie(ADMIN_CSRF_COOKIE, path="/")
    return resp


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


def _admin_page_html(path: Path) -> str:
    # Admin pages share one header (menu, logout, CSRF-aware fetch); it is
    # inserted where a page has the "admin-nav" marker comment.
    html = path.read_text(encoding="utf-8")
    marker = "<!--admin-nav-->"
    if marker in html:
        try:
            nav = (_STATIC_DIR / "admin_nav.html").read_text(encoding="utf-8")
        except OSError:
            nav = ""
        html = html.replace(marker, nav, 1)
    return html


@app.get("/admin", response_class=HTMLResponse)
def admin_home_page(_admin_auth: None = Depends(require_admin_auth)):
    path = _STATIC_DIR / "admin_home.html"
    try:
        return HTMLResponse(_admin_page_html(path))
    except Exception:
        logging.exception("admin dashboard unavailable")
        raise HTTPException(status_code=500, detail="admin dashboard unavailable")


@app.get("/admin/metrics/summary")
def admin_metrics_summary(_admin_auth: None = Depends(require_admin_auth)):
    from webapi.alerting import evaluate_alerts

    counters = metrics_registry.aggregate_snapshot()
    audit_failures = write_failure_count()
    if audit_failures:
        counters = dict(counters)
        counters["audit_write_failures_total"] = {"total": audit_failures}
    return {"counters": counters, "scope": metrics_registry.scope(), "alerts": evaluate_alerts({"counters": counters})}


@app.get("/admin/review", response_class=HTMLResponse)
def admin_review_page(_admin_auth: None = Depends(require_admin_auth)):
    path = _STATIC_DIR / "review_queue.html"
    try:
        return HTMLResponse(_admin_page_html(path))
    except Exception:
        logging.exception("review queue page unavailable")
        raise HTTPException(status_code=500, detail="review queue page unavailable")


@app.get("/admin/review/items")
def admin_review_items(
    request: Request = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    tenant_id: Optional[str] = None,
    limit: int = 100,
    _admin_auth: None = Depends(require_admin_auth),
):
    scope = _request_scope(request)
    if tenant_id:
        require_scope(scope, tenant_id)
    return _load_review_items(
        status=status,
        priority=priority,
        tenant_id=tenant_id,
        limit=limit,
        tenant_scope=scope,
    )


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


# --- Document ingestion UI + job status (admin-gated; dry-run, non-prod) ----


class IngestionDryRunRequest(BaseModel):
    inputs: List[str]
    expected_tenant: Optional[str] = None
    collection: Optional[str] = None


class RawDocumentIngestionRequest(BaseModel):
    inputs: List[str]
    expected_tenant: str
    collection: str
    execute: bool = False
    # true: validate, queue, return 202 with the job id (poll the job status).
    async_job: bool = False


@app.get("/admin/ingestion", response_class=HTMLResponse)
def ingestion_page(_admin_auth: None = Depends(require_admin_auth)):
    path = _STATIC_DIR / "ingestion.html"
    try:
        return HTMLResponse(_admin_page_html(path))
    except Exception:
        logging.exception("ingestion page unavailable")
        raise HTTPException(status_code=500, detail="ingestion page unavailable")


@app.post("/admin/ingestion/dry-run")
def ingestion_dry_run(req: IngestionDryRunRequest, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    inputs = [str(p) for p in (req.inputs or []) if str(p).strip()]
    if not inputs:
        raise HTTPException(status_code=400, detail="inputs is required")
    scope = _request_scope(request)
    if scope is not None:
        require_scope(scope, req.expected_tenant)
    # Refuse the production/default collection outright (never mutated anyway;
    # this is a dry-run validation only).
    if req.collection is not None and ingestion_jobs.is_production_collection(req.collection):
        raise HTTPException(status_code=400, detail="refusing production/default collection")
    try:
        return ingestion_jobs.run_dry_run(
            inputs, expected_tenant=req.expected_tenant, collection=req.collection
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/admin/ingestion/raw-documents")
def ingestion_raw_documents(
    req: RawDocumentIngestionRequest,
    request: Request = None,
    _admin_auth: None = Depends(require_admin_auth),
):
    inputs = [str(p) for p in (req.inputs or []) if str(p).strip()]
    if not inputs:
        raise HTTPException(status_code=400, detail="inputs is required")
    if not str(req.expected_tenant or "").strip():
        raise HTTPException(status_code=400, detail="expected_tenant is required")
    scope = _request_scope(request)
    require_scope(scope, req.expected_tenant)
    if req.execute:
        _audit_admin(scope, "ingestion_execute", tenant_id=req.expected_tenant, collection=req.collection,
                     files=len(inputs), async_job=bool(req.async_job))
    if not str(req.collection or "").strip():
        raise HTTPException(status_code=400, detail="collection is required")
    if ingestion_jobs.is_production_collection(req.collection):
        raise HTTPException(status_code=400, detail="refusing production/default collection")
    try:
        if req.async_job:
            queued = ingestion_jobs.submit_raw_document_job(
                inputs,
                expected_tenant=req.expected_tenant,
                collection=req.collection,
                execute=bool(req.execute),
            )
            return JSONResponse(status_code=202, content=queued)
        return ingestion_jobs.run_raw_document_job(
            inputs,
            expected_tenant=req.expected_tenant,
            collection=req.collection,
            execute=bool(req.execute),
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


_UPLOAD_SUFFIXES = {".pdf": (b"%PDF",), ".docx": (b"PK\x03\x04",), ".xlsx": (b"PK\x03\x04",),
                    ".pptx": (b"PK\x03\x04",), ".csv": ()}


def _upload_dir() -> Path:
    # Uploads go under INGEST_UPLOAD_DIR (default: <first ingest root>/uploads),
    # which must itself be inside INGEST_ALLOWED_ROOTS so the ingestion job
    # accepts the saved files.
    roots = ingestion_jobs.allowed_ingest_roots()
    if not roots:
        raise HTTPException(status_code=409, detail="ingest roots are not configured (INGEST_ALLOWED_ROOTS)")
    configured = str(os.getenv("INGEST_UPLOAD_DIR", "")).strip()
    base = Path(configured) if configured else roots[0] / "uploads"
    if not base.is_absolute():
        base = Path(config.BASE_DIR) / base
    resolved = base.resolve()
    if not any(resolved == root or resolved.is_relative_to(root) for root in roots):
        raise HTTPException(status_code=409, detail="INGEST_UPLOAD_DIR must be inside INGEST_ALLOWED_ROOTS")
    return resolved


_UPLOAD_PRUNE = {"last": 0.0}


def _prune_old_uploads(directory: Path) -> int:
    # INGEST_UPLOAD_RETENTION_DAYS (default 30, 0 = keep): uploaded files
    # older than that are removed, at most once an hour. Only files named
    # like uploads ("<12 hex>_<name>") and their emptied day folders.
    try:
        days = float(os.getenv("INGEST_UPLOAD_RETENTION_DAYS", "") or 30)
    except ValueError:
        days = 30.0
    now = time.time()
    if days <= 0 or now - _UPLOAD_PRUNE["last"] < 3600:
        return 0
    _UPLOAD_PRUNE["last"] = now
    removed = 0
    for path in directory.glob("*/*/*"):
        if path.is_file() and re.fullmatch(r"[0-9a-f]{12}_.+", path.name) and now - path.stat().st_mtime > days * 86400:
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
    for day_dir in directory.glob("*/*"):
        if day_dir.is_dir() and not any(day_dir.iterdir()):
            day_dir.rmdir()
    return removed


@app.put("/admin/ingestion/upload")
async def ingestion_upload(
    request: Request,
    filename: str,
    tenant: Optional[str] = None,
    _admin_auth: None = Depends(require_admin_auth),
):
    # Raw request body (no multipart dependency); size-limited by
    # INGEST_UPLOAD_MAX_BYTES in the hardening middleware and here.
    require_scope(_request_scope(request), normalize_tenant_id(tenant))
    name = Path(str(filename or "")).name
    suffix = Path(name).suffix.lower()
    if suffix not in _UPLOAD_SUFFIXES:
        raise HTTPException(status_code=400, detail="unsupported file type (pdf, docx, xlsx, pptx, csv)")
    safe_name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)[-120:] or f"upload{suffix}"
    tenant_segment = re.sub(r"[^A-Za-z0-9_.-]", "_", normalize_tenant_id(tenant))
    upload_root = _upload_dir()
    _prune_old_uploads(upload_root)
    target_dir = upload_root / tenant_segment / time.strftime("%Y%m%d")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{uuid.uuid4().hex[:12]}_{safe_name}"
    partial = target.with_name(f".{target.name}.part")
    limit = security_middleware.max_upload_bytes()
    digest = hashlib.sha256()
    size = 0
    head = b""
    try:
        with partial.open("wb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(status_code=413, detail="file too large")
                if len(head) < 8:
                    head += chunk[: 8 - len(head)]
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="empty file")
        signatures = _UPLOAD_SUFFIXES[suffix]
        if signatures and not any(head.startswith(sig) for sig in signatures):
            raise HTTPException(status_code=400, detail="file content does not match its extension")
        os.replace(partial, target)
    finally:
        if partial.exists():
            partial.unlink()
    return {"path": str(target), "filename": name, "bytes": size, "sha256": digest.hexdigest()}


def _job_in_scope(job: Dict[str, Any], scope) -> bool:
    return scope is None or str(job.get("expected_tenant") or "default") in scope


@app.get("/admin/ingestion/jobs")
def ingestion_jobs_list(request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    scope = _request_scope(request)
    return {"jobs": [job for job in ingestion_jobs.list_jobs() if _job_in_scope(job, scope)]}


@app.get("/admin/ingestion/jobs/{job_id}")
def ingestion_job_status(job_id: str, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    job = ingestion_jobs.get_job(job_id)
    if job is None or not _job_in_scope(job, _request_scope(request)):
        raise HTTPException(status_code=404, detail="job not found")
    return job


# --- Document deletion and promotion to the served collection (stage 15) -------


class DocumentDeleteRequest(BaseModel):
    tenant_id: str = Field(..., max_length=128)
    source_docs: List[str] = Field(..., min_length=1, max_length=500)
    # None: the served (production) collection; otherwise a staging collection.
    collection: Optional[str] = Field(None, max_length=128)
    execute: bool = False


class PromoteRequest(BaseModel):
    collection: str = Field(..., max_length=128)
    tenant_id: str = Field(..., max_length=128)
    execute: bool = False


@app.post("/admin/ingestion/documents/delete")
def ingestion_delete_documents(req: DocumentDeleteRequest, request: Request = None,
                               _admin_auth: None = Depends(require_admin_auth)):
    # Dry run unless execute=true: the report lists the chunks per document.
    from webapi import collection_admin

    scope = _request_scope(request)
    tenant = normalize_tenant_id(req.tenant_id)
    require_scope(scope, tenant)
    try:
        report = collection_admin.delete_documents(req.collection, tenant=tenant, source_docs=req.source_docs,
                                                   execute=req.execute)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if req.execute:
        _audit_admin(scope, "documents_deleted", tenant_id=tenant, collection=report["collection"],
                     documents=sorted(report["documents"]), chunks=report["chunks"])
    return report


@app.post("/admin/ingestion/promote")
def ingestion_promote(req: PromoteRequest, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    # Staging -> served collection for one tenant's documents (dry run unless execute=true).
    from webapi import collection_admin

    scope = _request_scope(request)
    tenant = normalize_tenant_id(req.tenant_id)
    require_scope(scope, tenant)
    try:
        report = collection_admin.promote_staging(req.collection, tenant=tenant, execute=req.execute)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if req.execute:
        _audit_admin(scope, "collection_promoted", tenant_id=tenant, collection=req.collection,
                     documents=report["documents"], chunks=report["chunks"])
    return report


# --- Contracts (7-3) ------------------------------------------------------------


class ContractImportRequest(BaseModel):
    tenant_id: str = Field("default", max_length=64)
    contract_id: str = Field(..., max_length=64)
    source_doc: str = Field("", max_length=512)
    extracted: Dict[str, Any]


class ContractFieldRequest(BaseModel):
    tenant_id: str = Field("default", max_length=64)
    name: str = Field(..., max_length=40)
    value: Optional[Any] = None
    note: Optional[str] = Field(None, max_length=500)


class ContractNotifyRequest(BaseModel):
    tenant_id: str = Field("default", max_length=64)
    dry_run: bool = True


class ContractDraftRequest(BaseModel):
    tenant_id: str = Field("default", max_length=64)
    kind: str = Field(..., max_length=40)
    mailbox: str = Field(..., max_length=320)


def _contract_http_error(exc: Exception) -> HTTPException:
    text = str(exc)
    if "not found" in text:
        return HTTPException(status_code=404, detail=text)
    if "already exists" in text or "changed by someone else" in text:
        return HTTPException(status_code=409, detail=text)
    return HTTPException(status_code=400, detail=text)


def _contract_scope(request, tenant_id: str):
    scope = _request_scope(request)
    require_scope(scope, tenant_id)
    return scope


def _contract_summary(contract: Dict[str, Any]) -> Dict[str, Any]:
    items = contracts.deadlines(contract)
    ahead = sorted((d for d in items if d["days_left"] >= 0), key=lambda d: d["date"])
    nearest = ahead[0] if ahead else (max(items, key=lambda d: d["date"]) if items else None)
    fields = contract["fields"]
    return {"contract_id": contract["contract_id"], "counterparty": fields["counterparty"]["value"], "title": fields["title"]["value"],
            "status": contracts.status_of(contract), "source_doc": contract["source_doc"], "nearest_deadline": nearest,
            "unconfirmed": sum(1 for f in fields.values() if f["value"] is not None and f["state"] == "extracted")}


@app.get("/admin/contracts", response_class=HTMLResponse)
def contracts_page(_admin_auth: None = Depends(require_admin_auth)):
    path = _STATIC_DIR / "contracts.html"
    try:
        return HTMLResponse(_admin_page_html(path))
    except Exception:
        logging.exception("contracts page unavailable")
        raise HTTPException(status_code=500, detail="contracts page unavailable")


@app.get("/admin/contracts/list")
def contracts_list(tenant_id: str = Query("default", max_length=64), request: Request = None,
                   _admin_auth: None = Depends(require_admin_auth)):
    _contract_scope(request, tenant_id)
    try:
        return {"contracts": [_contract_summary(c) for c in contracts.list_contracts(tenant_id)]}
    except contracts.ContractError as exc:
        raise _contract_http_error(exc)


@app.get("/admin/contracts/upcoming")
def contracts_upcoming(tenant_id: str = Query("default", max_length=64), within_days: int = Query(90, ge=0, le=730),
                       request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    _contract_scope(request, tenant_id)
    try:
        return {"deadlines": contracts.upcoming(tenant_id, within_days=within_days)}
    except contracts.ContractError as exc:
        raise _contract_http_error(exc)


@app.post("/admin/contracts/import")
def contracts_import(req: ContractImportRequest, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    scope = _contract_scope(request, req.tenant_id)
    try:
        out = contracts.import_extracted(req.tenant_id, req.contract_id, req.extracted, source_doc=req.source_doc,
                                         created_by=_admin_label(scope))
    except contracts.ContractError as exc:
        raise _contract_http_error(exc)
    _audit_admin(scope, "contract_imported", tenant_id=req.tenant_id, contract_id=req.contract_id,
                 rejected=sorted(out["rejected"]), kept=out["kept"])
    return {"contract": _contract_summary(out["contract"]), "rejected": out["rejected"], "kept": out["kept"]}


@app.post("/admin/contracts/notify")
def contracts_notify(req: ContractNotifyRequest, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    scope = _contract_scope(request, req.tenant_id)
    try:
        out = contract_notify.run(req.tenant_id, dry_run=req.dry_run)
    except contracts.ContractError as exc:
        raise _contract_http_error(exc)
    _audit_admin(scope, "contract_notify_run", tenant_id=req.tenant_id, dry_run=req.dry_run, due=out["due"], sent=out["sent"],
                 failed=out["failed"])
    return out


@app.get("/admin/contracts/{contract_id}")
def contracts_detail(contract_id: str, tenant_id: str = Query("default", max_length=64), request: Request = None,
                     _admin_auth: None = Depends(require_admin_auth)):
    _contract_scope(request, tenant_id)
    try:
        contract = contracts.get(tenant_id, contract_id)
    except contracts.ContractError as exc:
        raise _contract_http_error(exc)
    return {**contract, "history": contract["history"][-50:], "status": contracts.status_of(contract),
            "deadlines": contracts.deadlines(contract), "required": list(contracts.REQUIRED)}


@app.post("/admin/contracts/{contract_id}/field")
def contracts_set_field(contract_id: str, req: ContractFieldRequest, request: Request = None,
                        _admin_auth: None = Depends(require_admin_auth)):
    scope = _contract_scope(request, req.tenant_id)
    try:
        contract = contracts.set_field(req.tenant_id, contract_id, req.name, req.value, by=_admin_label(scope), note=req.note or "")
    except contracts.ContractError as exc:
        raise _contract_http_error(exc)
    _audit_admin(scope, "contract_field_set", tenant_id=req.tenant_id, contract_id=contract_id, field=req.name)
    return _contract_summary(contract)


@app.post("/admin/contracts/{contract_id}/confirm")
def contracts_confirm_field(contract_id: str, req: ContractFieldRequest, request: Request = None,
                            _admin_auth: None = Depends(require_admin_auth)):
    scope = _contract_scope(request, req.tenant_id)
    try:
        contract = contracts.confirm_field(req.tenant_id, contract_id, req.name, by=_admin_label(scope))
    except contracts.ContractError as exc:
        raise _contract_http_error(exc)
    _audit_admin(scope, "contract_field_confirmed", tenant_id=req.tenant_id, contract_id=contract_id, field=req.name)
    return _contract_summary(contract)


@app.post("/admin/contracts/{contract_id}/draft")
def contracts_draft(contract_id: str, req: ContractDraftRequest, request: Request = None,
                    _admin_auth: None = Depends(require_admin_auth)):
    scope = _contract_scope(request, req.tenant_id)
    try:
        client = outlook_connector.default_client()
        out = outlook_connector.draft_for_deadline(req.tenant_id, contract_id, req.kind, client=client, mailbox=req.mailbox,
                                                   by=_admin_label(scope))
    except contracts.ContractError as exc:
        raise _contract_http_error(exc)
    except source_registry.SourceError:
        raise HTTPException(status_code=409, detail="mail access is not configured (OUTLOOK_ACCESS_TOKEN or the client credentials)")
    except Exception:  # noqa: BLE001 - a Graph failure is reported without its details
        logging.exception("contract draft failed contract_id=%s", contract_id)
        raise HTTPException(status_code=502, detail="the draft could not be created")
    _audit_admin(scope, "contract_draft_created", tenant_id=req.tenant_id, contract_id=contract_id, kind=req.kind)
    return out


# --- Extraction review (6-3) --------------------------------------------------


class ExtractionLineRequest(BaseModel):
    tenant_id: str = Field("default", max_length=64)
    source_doc: str = Field(..., max_length=512)
    page: int = Field(..., ge=1)
    line_id: str = Field(..., max_length=32)
    text: Optional[str] = Field(None, max_length=2000)
    note: Optional[str] = Field(None, max_length=500)


class ExtractionDocRequest(BaseModel):
    tenant_id: str = Field("default", max_length=64)
    source_doc: str = Field(..., max_length=512)


def _extraction_http_error(exc: Exception) -> HTTPException:
    text = str(exc)
    if "no extraction record" in text or "no such line" in text or "not available" in text:
        return HTTPException(status_code=404, detail=text)
    return HTTPException(status_code=400, detail=text)


def _extraction_scope(request, tenant_id: str):
    scope = _request_scope(request)
    require_scope(scope, tenant_id)
    return scope


@app.get("/admin/extraction", response_class=HTMLResponse)
def extraction_page(_admin_auth: None = Depends(require_admin_auth)):
    path = _STATIC_DIR / "extraction_review.html"
    try:
        return HTMLResponse(_admin_page_html(path))
    except Exception:
        logging.exception("extraction review page unavailable")
        raise HTTPException(status_code=500, detail="extraction review page unavailable")


@app.get("/admin/extraction/docs")
def extraction_docs(tenant_id: str = Query("default", max_length=64), request: Request = None,
                    _admin_auth: None = Depends(require_admin_auth)):
    _extraction_scope(request, tenant_id)
    try:
        return {"documents": extraction_review.documents(tenant_id)}
    except extraction_store.ExtractionError as exc:
        raise _extraction_http_error(exc)


@app.get("/admin/extraction/items")
def extraction_items(
    tenant_id: str = Query("default", max_length=64), max_confidence: Optional[float] = Query(None, ge=0, le=100),
    include_reviewed: bool = False, source_doc: Optional[str] = Query(None, max_length=512),
    limit: int = Query(100, ge=1, le=500), request: Request = None, _admin_auth: None = Depends(require_admin_auth),
):
    _extraction_scope(request, tenant_id)
    try:
        return {"items": extraction_review.review_items(tenant_id, max_confidence=max_confidence,
                                                        include_reviewed=include_reviewed, source_doc=source_doc, limit=limit)}
    except extraction_store.ExtractionError as exc:
        raise _extraction_http_error(exc)


@app.get("/admin/extraction/page")
def extraction_page_data(
    source_doc: str = Query(..., max_length=512), page: int = Query(..., ge=1), tenant_id: str = Query("default", max_length=64),
    request: Request = None, _admin_auth: None = Depends(require_admin_auth),
):
    _extraction_scope(request, tenant_id)
    try:
        record = extraction_store.load_record(tenant_id, source_doc)
        entry = None if record is None else next((p for p in record["pages"] if p["page"] == page), None)
        if entry is None:
            raise extraction_store.ExtractionError("no extraction record for this page")
        decided = extraction_review._states(tenant_id, source_doc)
    except extraction_store.ExtractionError as exc:
        raise _extraction_http_error(exc)
    return {"source_doc": source_doc, "page": page, "width_px": entry["width_px"], "height_px": entry["height_px"],
            "confidence": entry["confidence"], "preview_available": bool(record.get("file_path")),
            "threshold": extraction_store.review_max_confidence(),
            "lines": [{**line, "state": decided.get((page, line["text"]), "open")} for line in entry["lines"]]}


@app.get("/admin/extraction/page-image")
def extraction_page_image(
    source_doc: str = Query(..., max_length=512), page: int = Query(..., ge=1), tenant_id: str = Query("default", max_length=64),
    request: Request = None, _admin_auth: None = Depends(require_admin_auth),
):
    _extraction_scope(request, tenant_id)
    try:
        record = extraction_store.load_record(tenant_id, source_doc)
        if record is None:
            raise extraction_store.ExtractionError("no extraction record for this document")
        png = extraction_store.render_page_png(record, page)
    except extraction_store.ExtractionError as exc:
        raise _extraction_http_error(exc)
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "private, max-age=60"})


def _extraction_decision(req: ExtractionLineRequest, request, action: str):
    scope = _extraction_scope(request, req.tenant_id)
    who = _admin_label(scope)
    try:
        if action == "correct":
            result = extraction_review.submit_correction(req.tenant_id, req.source_doc, req.page, req.line_id, req.text or "",
                                                         by=who, note=req.note or "")
        elif action == "confirm":
            result = extraction_review.confirm_line(req.tenant_id, req.source_doc, req.page, req.line_id, by=who, note=req.note or "")
        else:
            result = {"reverted": extraction_review.revert(req.tenant_id, req.source_doc, req.page, req.line_id)}
    except extraction_store.ExtractionError as exc:
        raise _extraction_http_error(exc)
    _audit_admin(scope, f"extraction_{action}ed",  # corrected / confirmed / reverted
                 tenant_id=req.tenant_id, source_doc=req.source_doc, page=req.page, line_id=req.line_id)
    return result


@app.post("/admin/extraction/correct")
def extraction_correct(req: ExtractionLineRequest, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    return _extraction_decision(req, request, "correct")


@app.post("/admin/extraction/confirm")
def extraction_confirm(req: ExtractionLineRequest, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    return _extraction_decision(req, request, "confirm")


@app.post("/admin/extraction/revert")
def extraction_revert(req: ExtractionLineRequest, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    return _extraction_decision(req, request, "revert")


@app.post("/admin/extraction/reingest")
def extraction_reingest(req: ExtractionDocRequest, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    scope = _extraction_scope(request, req.tenant_id)
    try:
        sources = extraction_review.request_reingest(req.tenant_id, req.source_doc)
    except extraction_store.ExtractionError as exc:
        raise _extraction_http_error(exc)
    _audit_admin(scope, "extraction_reingest_requested", tenant_id=req.tenant_id, source_doc=req.source_doc, sources=sources)
    return {"sources": sources, "note": "the document is ingested again by the next apply of those sources"
            if sources else "no registered source holds this document: upload it again"}


# --- Document sources (5-4) ---------------------------------------------------


class SourceCreateRequest(BaseModel):
    source_id: str = Field(..., max_length=48)
    tenant_id: str = Field(..., max_length=64)
    kind: str = Field("folder", max_length=16)
    path: Optional[str] = Field(None, max_length=1024)
    options: Optional[Dict[str, Any]] = None
    schedule_minutes: int = 0
    collection: Optional[str] = Field(None, max_length=128)


class SourceApplyRequest(BaseModel):
    approval: Optional[str] = Field(None, max_length=64)


class SourceApproveRequest(BaseModel):
    plan_sha256: str = Field(..., max_length=64)


def _source_http_error(exc: Exception) -> HTTPException:
    text = str(exc)
    if "not found" in text:
        return HTTPException(status_code=404, detail=text)
    if "already exists" in text or "changed by someone else" in text:
        return HTTPException(status_code=409, detail=text)
    return HTTPException(status_code=400, detail=text)


def _admin_label(scope) -> str:
    return "installation" if scope is None else ",".join(sorted(scope))


def _owned_source(source_id: str, scope) -> Dict[str, Any]:
    # A source outside the administrator's tenants is "not found", the same as one that does not exist.
    try:
        record = source_registry.get(source_id)
    except source_registry.SourceError as exc:
        raise _source_http_error(exc)
    if scope is not None and record["tenant_id"] not in scope:
        raise HTTPException(status_code=404, detail=f"source '{source_id}' not found")
    return record


def _source_row(record: Dict[str, Any]) -> Dict[str, Any]:
    # Never the server path or the options of the source.
    row = {key: record[key] for key in ("source_id", "tenant_id", "kind", "collection", "schedule_minutes", "enabled", "state")}
    try:
        plan = source_apply.plan_source(record["source_id"])
        row["pending"], row["guard"] = plan["counts"], source_apply.guard(plan)
    except source_registry.SourceError:
        row["pending"], row["guard"] = None, None
    return row


@app.get("/admin/sources", response_class=HTMLResponse)
def sources_page(_admin_auth: None = Depends(require_admin_auth)):
    path = _STATIC_DIR / "sources.html"
    try:
        return HTMLResponse(_admin_page_html(path))
    except Exception:
        logging.exception("sources page unavailable")
        raise HTTPException(status_code=500, detail="sources page unavailable")


@app.get("/admin/sources/list")
def sources_list(request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    scope = _request_scope(request)
    return {"sources": [_source_row(r) for r in source_registry.list_sources()
                        if scope is None or r["tenant_id"] in scope]}


@app.post("/admin/sources")
def sources_create(req: SourceCreateRequest, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    scope = _request_scope(request)
    require_scope(scope, req.tenant_id)
    try:
        record = source_registry.create(
            req.source_id, tenant_id=req.tenant_id, kind=req.kind, path=req.path, options=req.options,
            schedule_minutes=req.schedule_minutes, collection=req.collection, created_by=_admin_label(scope),
        )
    except source_registry.SourceError as exc:
        raise _source_http_error(exc)
    _audit_admin(scope, "source_created", tenant_id=req.tenant_id, source_id=req.source_id, kind=req.kind)
    return _source_row(record)


@app.post("/admin/sources/{source_id}/scan")
def sources_scan(source_id: str, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    scope = _request_scope(request)
    record = _owned_source(source_id, scope)
    try:
        result = source_sync.scan_source(source_id)
    except Exception as exc:  # noqa: BLE001 - a connector failure is reported without its details
        if isinstance(exc, source_registry.SourceError):
            raise _source_http_error(exc)
        logging.exception("source scan failed source_id=%s", source_id)
        raise HTTPException(status_code=502, detail="the source could not be scanned")
    _audit_admin(scope, "source_scanned", tenant_id=record["tenant_id"], source_id=source_id,
                 counts=result.get("counts"), error=result.get("error"))
    if "error" in result:
        return {"error": result["error"], "run_id": result["run_id"]}
    return {"run_id": result["run_id"], "counts": result["counts"], "total_files": result["total_files"],
            "skipped": {k: (len(v) if isinstance(v, list) else v) for k, v in result["skipped"].items()},
            "name_conflicts": len(result["name_conflicts"])}


@app.get("/admin/sources/{source_id}/plan")
def sources_plan(source_id: str, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    _owned_source(source_id, _request_scope(request))
    try:
        plan = source_apply.plan_source(source_id)
    except source_registry.SourceError as exc:
        raise _source_http_error(exc)
    plan["guard"] = source_apply.guard(plan)
    for key in ("added", "modified", "deleted"):
        plan[key] = plan[key][:200]  # the rest is in the change ledger
    return plan


@app.post("/admin/sources/{source_id}/apply")
def sources_apply(source_id: str, req: SourceApplyRequest, request: Request = None,
                  _admin_auth: None = Depends(require_admin_auth)):
    scope = _request_scope(request)
    record = _owned_source(source_id, scope)
    try:
        result = source_apply.apply_source(source_id, approval=req.approval, actor=_admin_label(scope))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except source_registry.SourceError as exc:
        raise _source_http_error(exc)
    except Exception:  # noqa: BLE001
        logging.exception("source apply failed source_id=%s", source_id)
        raise HTTPException(status_code=502, detail="the changes could not be applied")
    _audit_admin(scope, "source_applied", tenant_id=record["tenant_id"], source_id=source_id,
                 status=result.get("status"), counts=result.get("counts"), reasons=result.get("reasons"))
    return result


@app.post("/admin/sources/{source_id}/approve")
def sources_approve(source_id: str, req: SourceApproveRequest, request: Request = None,
                    _admin_auth: None = Depends(require_admin_auth)):
    scope = _request_scope(request)
    record = _owned_source(source_id, scope)
    try:
        result = source_apply.approve(source_id, req.plan_sha256, actor=_admin_label(scope))
    except source_registry.SourceError as exc:
        raise _source_http_error(exc)
    _audit_admin(scope, "source_approved", tenant_id=record["tenant_id"], source_id=source_id, plan_sha256=req.plan_sha256)
    return result


@app.post("/admin/sources/{source_id}/{action}")
def sources_switch(source_id: str, action: str, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    if action not in {"enable", "disable", "remove"}:
        raise HTTPException(status_code=404, detail="unknown action")
    scope = _request_scope(request)
    record = _owned_source(source_id, scope)
    try:
        if action == "remove":
            source_registry.remove(source_id)
            result: Dict[str, Any] = {"removed": source_id}
        else:
            result = _source_row(source_registry.set_enabled(source_id, action == "enable"))
    except source_registry.SourceError as exc:
        raise _source_http_error(exc)
    _audit_admin(scope, f"source_{action}d" if action != "remove" else "source_removed",
                 tenant_id=record["tenant_id"], source_id=source_id)
    return result


@app.get("/admin/sources/{source_id}/changes")
def sources_changes(source_id: str, limit: int = Query(20, ge=1, le=200), request: Request = None,
                    _admin_auth: None = Depends(require_admin_auth)):
    _owned_source(source_id, _request_scope(request))
    try:
        entries = source_changes.read_entries(source_id, limit)
    except source_changes.LedgerError as exc:
        raise _source_http_error(exc)
    return {"entries": [{k: e.get(k) for k in ("seq", "at", "kind", "run_id", "summary")} for e in entries]}


# --- Synonym candidate review (N8) -------------------------------------------


class SynonymDecisionRequest(BaseModel):
    candidate_id: str = Field(..., max_length=64)
    status: str


class SynonymApplyRequest(BaseModel):
    profile_id: str = Field(..., max_length=64)


@app.get("/admin/synonyms", response_class=HTMLResponse)
def synonym_review_page(_admin_auth: None = Depends(require_admin_auth)):
    path = _STATIC_DIR / "synonym_review.html"
    try:
        return HTMLResponse(_admin_page_html(path))
    except Exception:
        logging.exception("synonym review page unavailable")
        raise HTTPException(status_code=500, detail="synonym review page unavailable")


@app.get("/admin/synonyms/candidates")
def synonym_candidates(request: Request = None, status: Optional[str] = None, limit: int = 200,
                       _admin_auth: None = Depends(require_admin_auth)):
    # Candidates come from every tenant's corpus: installation administrator only.
    require_global(_request_scope(request))
    if status and status not in synonym_review.STATUSES:
        raise HTTPException(status_code=400, detail="invalid status")
    return synonym_review.list_candidates(status=status, limit=limit)


@app.post("/admin/synonyms/decision")
def synonym_decision(req: SynonymDecisionRequest, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    scope = _request_scope(request)
    require_global(scope)
    _audit_admin(scope, "synonym_decision", candidate_id=req.candidate_id, status=req.status)
    try:
        return synonym_review.decide(req.candidate_id, req.status, decided_by="admin")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except KeyError:
        raise HTTPException(status_code=404, detail="candidate not found")


@app.post("/admin/synonyms/apply")
def synonym_apply(req: SynonymApplyRequest, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    scope = _request_scope(request)
    require_global(scope)
    _audit_admin(scope, "synonym_apply", profile_id=req.profile_id)
    try:
        return synonym_review.apply_to_profile(req.profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# --- OIDC session revocation (6-3) ----------------------------------------------


class SessionRevokeRequest(BaseModel):
    key_fingerprint: str = Field(..., min_length=6, max_length=64)


@app.post("/admin/sessions/revoke")
def revoke_sessions(req: SessionRevokeRequest, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    # Every OIDC session of this identity issued until now stops working
    # (fingerprints are what audit records show as actor.key_fingerprint).
    scope = _request_scope(request)
    require_global(scope)
    oidc_auth.revoke_identity(req.key_fingerprint)
    _audit_admin(scope, "sessions_revoked", key_fingerprint=req.key_fingerprint)
    return {"ok": True, "key_fingerprint": req.key_fingerprint}


_CHAT_SAFE_PROFILE = "production_safe"


def resolve_chat_runtime_profile(tenant_id, *, customer_id=None) -> Optional[Dict[str, Any]]:
    # Default OFF: returns None and the chat path runs unchanged. When
    # CHAT_USE_TENANT_PROFILE is true, resolve the per-tenant product profile
    # using the already-authenticated tenant context and return a compact
    # runtime descriptor. Any resolution/config problem fails closed to the
    # safest profile (production_safe). This never bypasses tenant
    # authorization or isolation, and never touches guard, cross-encoder, or
    # distance settings — it only applies the resolved profile's candidate
    # limit and records the safe profile identifier.
    if not getattr(config, "CHAT_USE_TENANT_PROFILE", False):
        return None

    profile_name = _CHAT_SAFE_PROFILE
    decision = "fallback_safe"
    try:
        resolution = resolve_tenant_product_profile(
            tenant_id or "default", customer_id=customer_id, strict=False
        )
        decision = str(resolution.get("decision") or "") or "resolved"
        resolved = resolution.get("resolved_profile")
        # disabled / rejected / unknown → no resolved_profile → safest profile.
        profile_name = (
            str(resolved or resolution.get("default_profile") or _CHAT_SAFE_PROFILE).strip()
            or _CHAT_SAFE_PROFILE
        )
    except Exception:
        logging.exception("chat tenant profile resolution failed; using safe profile")
        profile_name = _CHAT_SAFE_PROFILE
        decision = "resolution_error"

    def _load_limit(name: str):
        # Returns (ok, max_candidates_internal). ok is False when the profile
        # cannot be loaded as itself — load_product_profile silently falls back
        # to the default profile for unknown names, which we treat as invalid
        # so resolution fails closed to the safest profile rather than serving
        # a different (possibly more permissive) profile.
        try:
            profile = load_product_profile(name)
            policy = build_route_policy(profile)
            # Never serve a profile that auto-answers similar candidates.
            policy.setdefault("answer_policy", {})["allow_similar_auto_answer"] = False
            if str(policy.get("profile_name") or "") != name:
                return False, None
            limits = policy.get("limits") if isinstance(policy.get("limits"), dict) else {}
            raw_limit = limits.get("max_candidates_internal")
            return True, (raw_limit if isinstance(raw_limit, int) and raw_limit > 0 else None)
        except Exception:
            logging.exception("chat product profile load failed for %s; using safe profile", name)
            return False, None

    ok, max_internal = _load_limit(profile_name)
    if not ok:
        profile_name = _CHAT_SAFE_PROFILE
        decision = "invalid_profile_fallback_safe"
        ok_safe, max_internal = _load_limit(_CHAT_SAFE_PROFILE)
        if not ok_safe:
            max_internal = None

    return {
        "profile_name": profile_name,
        "max_candidates_internal": max_internal,
        "decision": decision,
    }


def _apply_chat_profile_top_k(effective_top_k: int, runtime_profile: Optional[Dict[str, Any]]) -> int:
    if not runtime_profile:
        return effective_top_k
    limit = runtime_profile.get("max_candidates_internal")
    if isinstance(limit, int) and limit > 0:
        return max(1, min(effective_top_k, limit))
    return effective_top_k


def _record_chat_profile_metric(runtime_profile: Optional[Dict[str, Any]]) -> None:
    if runtime_profile and runtime_profile.get("profile_name"):
        # Safe enum label (a product profile name); never keys or query text.
        metrics_registry.increment("chat_tenant_profile_total", str(runtime_profile["profile_name"]))


def _annotate_chat_profile(event: Dict[str, Any], runtime_profile: Optional[Dict[str, Any]]) -> None:
    # Only adds safe identifiers (profile name + decision) when the runtime
    # profile is active; leaves the audit event untouched when default-off.
    if not runtime_profile:
        return
    event["product_profile"] = runtime_profile.get("profile_name")
    event["tenant_profile_decision"] = runtime_profile.get("decision")




def _resolve_staging_collection(collection: Optional[str], *, tenant_id: str) -> Optional[str]:
    name = str(collection or "").strip()
    if not name:
        return None
    if ingestion_jobs.is_production_collection(name):
        raise HTTPException(status_code=400, detail="refusing production/default collection for staging query")
    try:
        ingestion_jobs.validate_collection_name(name)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid staging collection name")
    status = ingestion_jobs.staging_collection_status(name, tenant_id=tenant_id)
    if status.get("allowed"):
        return name
    reason = str(status.get("reason") or "staging_collection_unavailable")
    if reason == "staging_collection_forbidden_for_tenant":
        raise HTTPException(status_code=403, detail="staging collection is not authorized for this tenant")
    if reason == "staging_collection_unknown":
        # Unregistered collections are refused even if they exist in the
        # vectorstore: their owner tenant is unknown.
        raise HTTPException(status_code=404, detail="staging collection is not available for query")
    raise HTTPException(status_code=409, detail="staging collection is not ready for query")


def _collection_meta(collection_name: Optional[str]) -> Dict[str, Any]:
    if not collection_name:
        return {"query_collection_mode": "served_default", "query_collection": None}
    return {"query_collection_mode": "staging", "query_collection": collection_name}


def _rag_profiles_allowed_for_tenant(tenant_id: str) -> frozenset:
    # RAG_PROFILE_TENANT_MAP: tenant_a=profile1|profile2,tenant_b=profile3
    allowed: frozenset = frozenset()
    for tenant, profiles in _parse_tenant_map(str(os.getenv("RAG_PROFILE_TENANT_MAP", ""))).items():
        if tenant == tenant_id:
            allowed = allowed | profiles
    return allowed


def _resolve_rag_profile_id(req: ChatRequest, staging_collection: Optional[str], tenant_id: str = "default") -> str:
    requested = str(req.rag_profile_id or "").strip()
    if requested:
        if not is_valid_rag_profile_id(requested):
            raise HTTPException(status_code=400, detail="invalid rag_profile_id")
        if requested != "default" and requested not in _rag_profiles_allowed_for_tenant(tenant_id):
            raise HTTPException(status_code=403, detail="rag_profile_id is not allowed for this tenant")
        return requested
    # Staging collections no longer imply a demo profile; select one explicitly
    # with rag_profile_id (allowed per tenant via RAG_PROFILE_TENANT_MAP).
    return "default"


def _conversation_question(req: ChatRequest, api_auth, tenant_id: str):
    """(question used for retrieval, trace or None) — 5-11 follow-up completion."""
    enabled = str(os.getenv("CONVERSATION_CONTEXT_ENABLED", "")).strip().lower() in {"1", "true", "yes", "on"}
    if not (enabled and req.thread_id and conversation_store.conversation_history_enabled()):
        return req.question, None
    turns = conversation_store.get_thread(tenant_id, _conversation_identity(api_auth), req.thread_id)
    result = conversation_context.contextualize(req.question, turns)
    if not result["completed"]:
        return req.question, None
    return result["question"], {
        "original_question": req.question,
        "contextualized_question": result["question"],
        "added_terms": result["added_terms"],
    }


def _annotate_collection_event(event: Dict[str, Any], collection_name: Optional[str]) -> None:
    if collection_name:
        event["query_collection"] = collection_name
        event["query_collection_mode"] = "staging"


@app.post("/chat")
def chat(req: ChatRequest, api_auth: ApiAuthContext = Depends(require_api_auth_rate_limited)):
    global _total_requests, _error_requests
    _total_requests += 1
    metrics_registry.note_request()
    tenant_id = normalize_tenant_id(req.tenant_id)
    enforce_tenant_authorization(api_auth, tenant_id)
    set_audit_actor(api_auth)
    staging_collection = _resolve_staging_collection(req.staging_collection, tenant_id=tenant_id)
    rag_profile_id = _resolve_rag_profile_id(req, staging_collection, tenant_id)
    question_for_answer, conversation_trace = _conversation_question(req, api_auth, tenant_id)
    try:
        approved = None if staging_collection else _approved_qa_lookup(req.question, tenant_id=tenant_id)
        if approved is not None:
            payload = _approved_chat_payload(approved)
            approved_mode = _approved_answer_mode(approved)
            approved_trace_id = req.trace_id or uuid.uuid4().hex
            payload["request_id"] = approved_trace_id
            payload["trace_id"] = approved_trace_id
            append_audit_event(
                "chat",
                {
                    "request_id": approved_trace_id,
                    "trace_id": approved_trace_id,
                    "tenant_id": approved.tenant_id,
                    "question": req.question,
                    **_approved_audit_fields(approved, req.question),
                },
            )
            _record_chat_outcome_metrics(answer_mode=approved_mode)
            return payload

        approved_exact_payload = None if staging_collection else _approved_exact_chat_lookup(req.question, tenant_id)
        if approved_exact_payload is not None:
            approved_exact_payload.update(_collection_meta(staging_collection))
            append_audit_event(
                "chat",
                {
                    "request_id": req.trace_id,
                    "trace_id": req.trace_id,
                    "tenant_id": tenant_id,
                    "question": req.question,
                    "normalized_question": approved_exact_payload.get("normalized_question"),
                    "answer_mode": "approved_exact_match",
                    "approved_qa_id": approved_exact_payload.get("approved_qa_id"),
                    "retrieval_source": "approved_qa_exact",
                    "citations_count": len(approved_exact_payload.get("citations") or []),
                },
            )
            _record_chat_outcome_metrics(answer_mode="approved_exact_match")
            return approved_exact_payload

        runtime_profile = resolve_chat_runtime_profile(tenant_id)
        effective_top_k = _apply_chat_profile_top_k(req.top_k or config.TOP_K, runtime_profile)
        effective_max_context_chars = req.max_context_chars or config.MAX_CONTEXT_CHARS
        _record_chat_profile_metric(runtime_profile)
        if req.deep:
            effective_top_k = min(effective_top_k * 2, _MAX_TOP_K)
        cache_key = None
        if answer_cache.enabled() and not staging_collection and rag_profile_id == "default" and not req.deep:
            cache_key = answer_cache.build_key(
                question_for_answer,
                top_k=effective_top_k,
                max_context_chars=effective_max_context_chars,
                tenant_id=tenant_id,
                rag_profile_id=rag_profile_id,
            )
            cached = answer_cache.get(cache_key)
            if cached is not None:
                append_audit_event(
                    "chat",
                    {
                        "request_id": req.trace_id,
                        "trace_id": req.trace_id,
                        "tenant_id": tenant_id,
                        "question": req.question,
                        "guard_reason": cached.get("guard_reason"),
                        "used_fallback": cached.get("used_fallback"),
                        "citations_count": len(cached.get("citations") or []),
                        "cache_hit": True,
                    },
                )
                metrics_registry.increment("chat_cache_hit_total")
                # The stored payload carries its answer mode; a cached
                # fallback is not counted as a grounded answer.
                _record_chat_outcome_metrics(answer_mode=str(cached.get("answer_mode") or "grounded"))
                # The stored copy holds no per-request fields: this request's own
                # ids and conversation context are added back (stage 14).
                hit = dict(cached)
                if req.trace_id:
                    hit["trace_id"] = req.trace_id
                if conversation_trace:
                    hit["conversation_context"] = conversation_trace
                return hit

        client = (
            ensure_openai_client(base_url=config.OPENAI_BASE_URL)
            if config.resolve_chat_generation_mode() == "llm"
            else None
        )
        answer_kwargs: Dict[str, Any] = {
            "client": client,
            "top_k": effective_top_k,
            "max_context_chars": effective_max_context_chars,
            "tenant_id": tenant_id,
        }
        if staging_collection:
            answer_kwargs["collection_name"] = staging_collection
        answer_kwargs["rag_profile_id"] = rag_profile_id
        with search_mode.deep_mode(bool(req.deep)):
            ans, trace = answer_query_with_trace(question_for_answer, **answer_kwargs)
        chat_event = {
            "request_id": trace.get("request_id"),
            "trace_id": req.trace_id or trace.get("request_id"),
            "tenant_id": tenant_id,
            "question": req.question,
            "normalized_query": trace.get("normalized_query"),
            "intent": trace.get("intent") or ans.intent,
            "guard_reason": trace.get("final_guard_reason") or ans.guard_reason,
            "used_fallback": trace.get("final_used_fallback", ans.used_fallback),
            "citations_count": trace.get("citations_count", len(ans.citations)),
            "top_source_docs": _top_source_docs(trace.get("after_rerank")),
            "latency_ms": trace.get("latency_ms"),
            "cache_hit": False,
        }
        _annotate_collection_event(chat_event, staging_collection)
        _annotate_chat_profile(chat_event, runtime_profile)
        append_audit_event("chat", chat_event)
        _record_chat_outcome_metrics(
            answer_mode=trace.get("answer_mode")
            or ("fallback" if (ans.guard_reason or ans.used_fallback) else "grounded"),
            guard_reason=trace.get("final_guard_reason") or ans.guard_reason,
            used_fallback=bool(trace.get("final_used_fallback", ans.used_fallback)),
        )
        payload = ans.to_dict()
        payload.update(_collection_meta(staging_collection))
        payload["answer_mode"] = trace.get("answer_mode") or (
            "fallback" if (ans.guard_reason or ans.used_fallback) else "grounded"
        )
        payload["chat_generation_mode"] = trace.get(
            "chat_generation_mode", config.resolve_chat_generation_mode()
        )
        payload["rag_profile_id"] = trace.get("rag_profile_id", rag_profile_id)
        if conversation_trace:
            payload["conversation_context"] = conversation_trace
        payload["question_type"] = trace.get("question_type", "other")
        payload["interpretation"] = trace.get("interpretation")
        payload["profile_validation"] = trace.get(
            "profile_validation",
            {
                "passed": True,
                "skipped": True,
                "matched_rule_id": "",
                "question_type": trace.get("question_type", "other"),
                "missing_any": [],
                "missing_all": [],
                "forbidden_hits": [],
                "fallback_if_failed": False,
                "reason": "profile validation trace unavailable",
            },
        )
        if cache_key is not None and ans.guard_reason is None and not ans.used_fallback:
            # Only clean grounded answers are cacheable; guard/no-answer and
            # extractive-fallback responses are never cached.
            # Stage 14: without the caller's conversation context (it holds that
            # user's earlier question) or request ids; another caller in the
            # tenant asking the same wording must not see them.
            answer_cache.put(cache_key, {
                k: v for k, v in payload.items()
                if k not in {"conversation_context", "trace_id", "request_id", "feedback_token"}
            })
        return payload
    except Exception as exc:
        _error_requests += 1
        metrics_registry.note_error()
        generation_error = _generation_error_payload(exc)
        append_audit_event(
            "chat",
            {
                "request_id": None,
                "trace_id": req.trace_id,
                "tenant_id": tenant_id,
                "question": req.question,
                "error": "chat generation unavailable" if generation_error else "internal error",
                "error_type": generation_error[1].get("error_type") if generation_error else None,
            },
        )
        logging.exception("chat failed trace_id=%s", req.trace_id)
        _record_provider_error_metric(generation_error)
        if generation_error:
            return JSONResponse(status_code=generation_error[0], content=generation_error[1])
        raise HTTPException(status_code=500, detail="internal error")


def _sse_event(name: str, payload: Dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.post("/chat/stream")
def chat_stream(req: ChatRequest, api_auth: ApiAuthContext = Depends(require_api_auth_rate_limited)):
    global _total_requests
    _total_requests += 1
    metrics_registry.note_request()
    tenant_id = normalize_tenant_id(req.tenant_id)
    # Raise before the StreamingResponse is constructed so an unauthorized
    # tenant gets a plain 403 and no stream ever starts.
    enforce_tenant_authorization(api_auth, tenant_id)
    set_audit_actor(api_auth)
    staging_collection = _resolve_staging_collection(req.staging_collection, tenant_id=tenant_id)
    # Stage 14: the same profile resolution as /chat (400/403 before the stream starts).
    rag_profile_id = _resolve_rag_profile_id(req, staging_collection, tenant_id)
    question_for_answer, conversation_trace = _conversation_question(req, api_auth, tenant_id)

    def _events():
        global _error_requests
        set_audit_actor(api_auth)
        try:
            approved = None if staging_collection else _approved_qa_lookup(req.question, tenant_id=tenant_id)
            if approved is not None:
                payload = _approved_chat_payload(approved)
                approved_mode = _approved_answer_mode(approved)
                approved_trace_id = req.trace_id or uuid.uuid4().hex
                payload["request_id"] = approved_trace_id
                payload["trace_id"] = approved_trace_id
                append_audit_event(
                    "chat",
                    {
                        "request_id": approved_trace_id,
                        "trace_id": approved_trace_id,
                        "tenant_id": approved.tenant_id,
                        "question": req.question,
                        **_approved_audit_fields(approved, req.question),
                    },
                )
                _record_chat_outcome_metrics(answer_mode=approved_mode)
                yield _sse_event("approved", payload)
                return

            approved_exact_payload = None if staging_collection else _approved_exact_chat_lookup(req.question, tenant_id)
            if approved_exact_payload is not None:
                approved_exact_payload.update(_collection_meta(staging_collection))
                append_audit_event(
                    "chat",
                    {
                        "request_id": req.trace_id,
                        "trace_id": req.trace_id,
                        "tenant_id": tenant_id,
                        "question": req.question,
                        "normalized_question": approved_exact_payload.get("normalized_question"),
                        "answer_mode": "approved_exact_match",
                        "approved_qa_id": approved_exact_payload.get("approved_qa_id"),
                        "retrieval_source": "approved_qa_exact",
                        "citations_count": len(approved_exact_payload.get("citations") or []),
                        "streamed": True,
                    },
                )
                _record_chat_outcome_metrics(answer_mode="approved_exact_match")
                yield _sse_event("approved", approved_exact_payload)
                return

            runtime_profile = resolve_chat_runtime_profile(tenant_id)
            effective_top_k = _apply_chat_profile_top_k(req.top_k or config.TOP_K, runtime_profile)
            effective_max_context_chars = req.max_context_chars or config.MAX_CONTEXT_CHARS
            _record_chat_profile_metric(runtime_profile)
            if req.deep:
                effective_top_k = min(effective_top_k * 2, _MAX_TOP_K)
            # Stage 14: the answer cache of /chat (same key, same rules); a deep request never uses it.
            cache_key = None
            if answer_cache.enabled() and not staging_collection and rag_profile_id == "default" and not req.deep:
                cache_key = answer_cache.build_key(
                    question_for_answer, top_k=effective_top_k,
                    max_context_chars=effective_max_context_chars, tenant_id=tenant_id,
                    rag_profile_id=rag_profile_id,
                )
                cached = answer_cache.get(cache_key)
                if cached is not None:
                    hit = dict(cached)
                    if req.trace_id:
                        hit["trace_id"] = req.trace_id
                    if conversation_trace:
                        hit["conversation_context"] = conversation_trace
                    append_audit_event("chat", {
                        "request_id": req.trace_id, "trace_id": req.trace_id, "tenant_id": tenant_id,
                        "question": req.question, "guard_reason": hit.get("guard_reason"),
                        "used_fallback": hit.get("used_fallback"),
                        "citations_count": len(hit.get("citations") or []), "cache_hit": True, "streamed": True,
                    })
                    metrics_registry.increment("chat_cache_hit_total")
                    _record_chat_outcome_metrics(answer_mode=str(hit.get("answer_mode") or "grounded"))
                    yield _sse_event("final", hit)
                    return
            client = (
                ensure_openai_client(base_url=config.OPENAI_BASE_URL)
                if config.resolve_chat_generation_mode() == "llm"
                else None
            )
            result = None
            trace = None
            stream_kwargs: Dict[str, Any] = {
                "client": client,
                "top_k": effective_top_k,
                "max_context_chars": effective_max_context_chars,
                "tenant_id": tenant_id,
                "rag_profile_id": rag_profile_id,
            }
            if staging_collection:
                stream_kwargs["collection_name"] = staging_collection
            for name, payload in search_mode.iterate_in_mode(
                bool(req.deep), answer_query_stream(question_for_answer, **stream_kwargs)
            ):
                if name == "final":
                    result, trace = payload
                    final_payload = result.to_dict()
                    final_payload.update(_collection_meta(staging_collection))
                    if conversation_trace:
                        final_payload["conversation_context"] = conversation_trace
                    final_payload["answer_mode"] = trace.get("answer_mode")
                    final_payload["chat_generation_mode"] = trace.get(
                        "chat_generation_mode", config.resolve_chat_generation_mode()
                    )
                    final_payload["rag_profile_id"] = trace.get("rag_profile_id", rag_profile_id)
                    final_payload["question_type"] = trace.get("question_type", "other")
                    final_payload["interpretation"] = trace.get("interpretation")
                    if "profile_validation" in trace:
                        final_payload["profile_validation"] = trace["profile_validation"]
                    if cache_key is not None and result.guard_reason is None and not result.used_fallback:
                        answer_cache.put(cache_key, {
                            k: v for k, v in final_payload.items()
                            if k not in {"conversation_context", "trace_id", "request_id", "feedback_token"}
                        })
                    yield _sse_event("final", final_payload)
                else:
                    yield _sse_event(name, payload)
            if result is not None and trace is not None:
                stream_event = {
                    "request_id": trace.get("request_id"),
                    "trace_id": req.trace_id or trace.get("request_id"),
                    "tenant_id": tenant_id,
                    "question": req.question,
                    "normalized_query": trace.get("normalized_query"),
                    "intent": trace.get("intent") or result.intent,
                    "guard_reason": trace.get("final_guard_reason") or result.guard_reason,
                    "used_fallback": trace.get("final_used_fallback", result.used_fallback),
                    "citations_count": trace.get("citations_count", len(result.citations)),
                    "top_source_docs": _top_source_docs(trace.get("after_rerank")),
                    "latency_ms": trace.get("latency_ms"),
                    "answer_mode": trace.get("answer_mode"),
                    "streamed": True,
                }
                _annotate_collection_event(stream_event, staging_collection)
                _annotate_chat_profile(stream_event, runtime_profile)
                append_audit_event("chat", stream_event)
                _record_chat_outcome_metrics(
                    answer_mode=trace.get("answer_mode"),
                    guard_reason=trace.get("final_guard_reason") or result.guard_reason,
                    used_fallback=bool(trace.get("final_used_fallback", result.used_fallback)),
                )
        except Exception as exc:
            _error_requests += 1
            metrics_registry.note_error()
            generation_error = _generation_error_payload(exc)
            _record_provider_error_metric(generation_error)
            content = (
                generation_error[1]
                if generation_error
                else {"detail": "internal error", "error_type": None}
            )
            append_audit_event(
                "chat",
                {
                    "request_id": None,
                    "trace_id": req.trace_id,
                    "tenant_id": tenant_id,
                    "question": req.question,
                    "error": "chat generation unavailable" if generation_error else "internal error",
                    "error_type": content.get("error_type"),
                    "streamed": True,
                },
            )
            logging.exception("chat_stream failed trace_id=%s", req.trace_id)
            yield _sse_event("error", content)

    return StreamingResponse(_events(), media_type="text/event-stream")


@app.post("/chat/product-preview")
def chat_product_preview(req: ProductPreviewChatRequest, api_auth: ApiAuthContext = Depends(require_api_auth_rate_limited)):
    global _total_requests, _error_requests
    _total_requests += 1
    metrics_registry.note_request()
    started = time.time()
    user_query = (req.query if req.query is not None else req.message) or ""
    user_query = user_query.strip()
    if not user_query:
        raise HTTPException(status_code=400, detail="query or message is required")

    request_id = str(uuid.uuid4())
    trace_id = request_id
    tenant_id = (req.tenant_id or "default").strip() or "default"
    enforce_tenant_authorization(api_auth, tenant_id)
    set_audit_actor(api_auth)
    top_k = max(1, min(int(req.top_k or 3), 10))
    keyword_profile = req.keyword_profile
    threshold_profile = req.threshold_profile
    rerank_profile = req.rerank_profile
    keyword_profile_path = _product_preview_profile_path(
        keyword_profile, _PRODUCT_PREVIEW_KEYWORD_PROFILES, "keyword_profile"
    )
    threshold_profile_path = _product_preview_profile_path(
        threshold_profile, _PRODUCT_PREVIEW_THRESHOLD_PROFILES, "threshold_profile"
    )
    if (keyword_profile_path or threshold_profile_path) and not caller_has_role(api_auth, ROLE_OPERATOR):
        raise HTTPException(status_code=403, detail="profile selection requires operator role")
    if _is_non_serving_product_profile(req.product_profile) and not caller_has_role(api_auth, ROLE_OPERATOR):
        raise HTTPException(status_code=403, detail="non-serving product profile requires operator role")
    requested_apply_feedback_preview = bool(req.apply_feedback_preview)
    requested_apply_feature_rerank = bool(req.apply_feature_rerank)
    policy_context = _product_policy_context(
        product_profile=req.product_profile,
        product_profile_overrides=req.product_profile_overrides,
        requested_top_k=top_k,
        requested_feedback_preview=requested_apply_feedback_preview,
        requested_feature_rerank=requested_apply_feature_rerank,
        use_tenant_profile=bool(req.use_tenant_profile),
        tenant_id=tenant_id,
        customer_id=req.customer_id,
    )
    effective_top_k = int(policy_context["effective_top_k"])
    display_top_k = int(policy_context["display_top_k"])
    product_policy_meta = policy_context["metadata"]
    apply_feedback_preview = bool(policy_context["effective_apply_feedback_preview"])
    apply_feature_rerank = bool(policy_context["effective_apply_feature_rerank"])
    feature_rerank_profile = req.feature_rerank_profile
    feedback_preview_meta = _feedback_preview_request_metadata(
        apply_feedback_preview=apply_feedback_preview,
        rerank_profile=rerank_profile,
    )
    feature_rerank_meta = _feature_rerank_request_metadata(
        apply_feature_rerank=apply_feature_rerank,
        feature_rerank_profile=feature_rerank_profile,
    )
    feedback_token = generate_feedback_token()

    try:
        if bool(policy_context.get("tenant_blocked")):
            latency_ms = round((time.time() - started) * 1000, 3)
            tenant_resolution = policy_context.get("tenant_resolution") if isinstance(policy_context.get("tenant_resolution"), dict) else {}
            audit_event = build_audit_event(
                request_id=request_id,
                trace_id=trace_id,
                tenant_id=tenant_id,
                user_query=user_query,
                answer_mode=ANSWER_MODE_FALLBACK_NO_ANSWER,
                selected_qa_id=None,
                candidate_ids=[],
                decision_route=CONFIDENCE_ROUTE_NO_ANSWER,
                keyword_profile=keyword_profile,
                threshold_profile=threshold_profile,
                latency_ms=latency_ms,
                feedback_token=feedback_token,
            )
            decision = _product_preview_decision_metadata(
                route=CONFIDENCE_ROUTE_NO_ANSWER,
                candidate_count=0,
                top_k=top_k,
                keyword_profile=keyword_profile,
                threshold_profile=threshold_profile,
                exact_match_checked=False,
                auto_answer_suppressed_for_similar_candidates=True,
                audit_event=audit_event,
                feedback_preview=feedback_preview_meta,
                feature_rerank=feature_rerank_meta,
                product_policy=product_policy_meta,
            )
            warnings = ["tenant_profile_resolution_blocked"]
            warnings.extend(policy_context["warnings"])
            warnings.extend(
                _append_product_preview_audit(
                    decision,
                    _product_preview_audit_payload(
                        audit_event,
                        candidate_count=0,
                        top_k=top_k,
                        auto_answer_suppressed_for_similar_candidates=True,
                        exact_match_checked=False,
                        feedback_preview=feedback_preview_meta,
                        feature_rerank=feature_rerank_meta,
                        product_policy=product_policy_meta,
                    ),
                )
            )
            profile_info = {
                "keyword_profile": keyword_profile,
                "threshold_profile": threshold_profile,
                "rerank_profile": rerank_profile,
                "apply_feedback_preview": requested_apply_feedback_preview,
                "feedback_preview_applied": False,
                "apply_feature_rerank": requested_apply_feature_rerank,
                "feature_rerank_profile": feature_rerank_profile,
                "feature_rerank_applied": False,
            }
            profile_info.update(_product_profile_info_metadata(product_policy_meta))
            if tenant_resolution.get("decision") == "disabled":
                warnings.append("tenant_disabled")
            return build_product_answer_envelope(
                request_id=request_id,
                trace_id=trace_id,
                tenant_id=tenant_id,
                answer_mode=ANSWER_MODE_FALLBACK_NO_ANSWER,
                answer_text="",
                confidence_route=CONFIDENCE_ROUTE_NO_ANSWER,
                citations=[],
                candidates=[],
                decision=decision,
                profile_info=profile_info,
                warnings=warnings,
                feedback_token=feedback_token,
            )

        approved = _approved_qa_lookup(user_query, tenant_id=tenant_id)
        if approved is not None:
            latency_ms = round((time.time() - started) * 1000, 3)
            approved_mode = (
                ANSWER_MODE_APPROVED_ALIAS_MATCH
                if approved.match_type == "alias"
                else ANSWER_MODE_APPROVED_EXACT_MATCH
            )
            audit_event = build_audit_event(
                request_id=request_id,
                trace_id=trace_id,
                tenant_id=tenant_id,
                user_query=user_query,
                answer_mode=approved_mode,
                selected_qa_id=approved.qa_id,
                candidate_ids=[],
                decision_route=CONFIDENCE_ROUTE_EXACT_MATCH,
                keyword_profile=keyword_profile,
                threshold_profile=threshold_profile,
                latency_ms=latency_ms,
                feedback_token=feedback_token,
                normalized_input_question=normalize_question_for_exact_match(user_query),
                matched_alias=approved.matched_alias,
                canonical_question=approved.question,
                retrieval_required=False,
                llm_used=False,
            )
            decision = _product_preview_decision_metadata(
                route=CONFIDENCE_ROUTE_EXACT_MATCH,
                candidate_count=0,
                top_k=top_k,
                keyword_profile=keyword_profile,
                threshold_profile=threshold_profile,
                exact_match_checked=True,
                auto_answer_suppressed_for_similar_candidates=False,
                audit_event=audit_event,
                feedback_preview=feedback_preview_meta,
                feature_rerank=feature_rerank_meta,
                product_policy=product_policy_meta,
            )
            audit_warnings = _append_product_preview_audit(
                decision,
                _product_preview_audit_payload(
                    audit_event,
                    candidate_count=0,
                    top_k=top_k,
                    auto_answer_suppressed_for_similar_candidates=False,
                    exact_match_checked=True,
                    feedback_preview=feedback_preview_meta,
                    feature_rerank=feature_rerank_meta,
                    product_policy=product_policy_meta,
                ),
            )
            response_warnings = list(policy_context["warnings"])
            response_warnings.extend(audit_warnings)
            profile_info = {
                "keyword_profile": keyword_profile,
                "threshold_profile": threshold_profile,
                "rerank_profile": rerank_profile,
                "apply_feedback_preview": requested_apply_feedback_preview,
                "apply_feature_rerank": requested_apply_feature_rerank,
                "feature_rerank_profile": feature_rerank_profile,
            }
            if product_policy_meta:
                profile_info.update(_product_profile_info_metadata(product_policy_meta))
            envelope = build_product_answer_envelope(
                request_id=request_id,
                trace_id=trace_id,
                tenant_id=tenant_id,
                answer_mode=approved_mode,
                answer_text=approved.approved_answer,
                confidence_route=CONFIDENCE_ROUTE_EXACT_MATCH,
                citations=_approved_product_citations(approved),
                candidates=[],
                decision=decision,
                profile_info=profile_info,
                warnings=response_warnings,
                feedback_token=feedback_token,
            )
            envelope["approved_qa_id"] = approved.qa_id
            envelope["canonical_approved_question"] = approved.question
            envelope["matched_alias"] = approved.matched_alias
            envelope["retrieval_required"] = False
            envelope["llm_used"] = False
            return envelope

        with _temporary_product_preview_profiles(
            keyword_profile_path=keyword_profile_path,
            threshold_profile_path=threshold_profile_path,
        ):
            client = _embedding_client()
            raw_candidates = approved_similar.search_approved_similar_candidates(
                user_query,
                client=client,
                tenant_id=tenant_id,
                top_k=effective_top_k,
            )
            preview_candidates, feedback_preview_meta, feedback_preview_warnings = apply_feedback_preview_rerank(
                raw_candidates[:effective_top_k],
                apply_feedback_preview=apply_feedback_preview,
                rerank_profile=rerank_profile,
            )
            preview_candidates, feature_rerank_meta, feature_rerank_warnings = _apply_feature_rerank_preview(
                user_query,
                preview_candidates,
                apply_feature_rerank=apply_feature_rerank,
                feature_rerank_profile=feature_rerank_profile,
            )
            decision_gate = approved_similar.decide_approved_similar_candidate(preview_candidates)

        candidate_contracts: List[Dict[str, Any]] = []
        for index, candidate in enumerate(preview_candidates[:display_top_k]):
            mapped = dict(candidate or {})
            if index == 0:
                mapped["decision_route"] = decision_gate.get("route")
            candidate_contracts.append(build_candidate_contract(mapped))

        candidate_ids = [
            str(candidate.get("qa_id"))
            for candidate in candidate_contracts
            if candidate.get("qa_id")
        ]
        latency_ms = round((time.time() - started) * 1000, 3)
        answer_mode = (
            ANSWER_MODE_APPROVED_SIMILAR_CANDIDATE_ONLY
            if candidate_contracts
            else ANSWER_MODE_FALLBACK_NO_ANSWER
        )
        confidence_route = (
            CONFIDENCE_ROUTE_CANDIDATE_ONLY
            if candidate_contracts
            else CONFIDENCE_ROUTE_NO_ANSWER
        )
        audit_event = build_audit_event(
            request_id=request_id,
            trace_id=trace_id,
            tenant_id=tenant_id,
            user_query=user_query,
            answer_mode=answer_mode,
            selected_qa_id=None,
            candidate_ids=candidate_ids,
            decision_route=str(decision_gate.get("route") or confidence_route),
            keyword_profile=keyword_profile,
            threshold_profile=threshold_profile,
            latency_ms=latency_ms,
            feedback_token=feedback_token,
        )
        decision = _product_preview_decision_metadata(
            route=confidence_route,
            candidate_count=len(candidate_contracts),
            top_k=display_top_k,
            keyword_profile=keyword_profile,
            threshold_profile=threshold_profile,
            exact_match_checked=True,
            auto_answer_suppressed_for_similar_candidates=bool(candidate_contracts),
            audit_event=audit_event,
            decision=decision_gate,
            feedback_preview=feedback_preview_meta,
            feature_rerank=feature_rerank_meta,
            product_policy=product_policy_meta,
        )
        warnings = (
            ["approved_similar_candidates_are_preview_only"]
            if candidate_contracts
            else ["no_approved_similar_candidate_found"]
        )
        warnings.extend(policy_context["warnings"])
        warnings.extend(feedback_preview_warnings)
        warnings.extend(feature_rerank_warnings)
        warnings.extend(
            _append_product_preview_audit(
                decision,
                _product_preview_audit_payload(
                    audit_event,
                    candidate_count=len(candidate_contracts),
                    top_k=display_top_k,
                    auto_answer_suppressed_for_similar_candidates=bool(candidate_contracts),
                    exact_match_checked=True,
                    feedback_preview=feedback_preview_meta,
                    feature_rerank=feature_rerank_meta,
                    product_policy=product_policy_meta,
                ),
            )
        )
        profile_info = {
            "keyword_profile": keyword_profile,
            "threshold_profile": threshold_profile,
            "rerank_profile": rerank_profile,
            "apply_feedback_preview": requested_apply_feedback_preview,
            "feedback_preview_applied": feedback_preview_meta.get("feedback_preview_applied"),
            "apply_feature_rerank": requested_apply_feature_rerank,
            "feature_rerank_profile": feature_rerank_profile,
            "feature_rerank_applied": feature_rerank_meta.get("feature_rerank_applied"),
        }
        if product_policy_meta:
            profile_info.update(_product_profile_info_metadata(product_policy_meta))
        return build_product_answer_envelope(
            request_id=request_id,
            trace_id=trace_id,
            tenant_id=tenant_id,
            answer_mode=answer_mode,
            answer_text="",
            confidence_route=confidence_route,
            citations=[],
            candidates=candidate_contracts,
            decision=decision,
            profile_info=profile_info,
            warnings=warnings,
            feedback_token=feedback_token,
        )
    except HTTPException:
        raise
    except Exception:
        _error_requests += 1
        metrics_registry.note_error()
        logging.exception("chat_product_preview failed trace_id=%s", trace_id)
        raise HTTPException(status_code=500, detail="internal error")


@app.post("/chat/feedback")
def chat_feedback(req: ProductFeedbackRequest, api_auth: ApiAuthContext = Depends(require_api_auth_rate_limited)):
    enforce_tenant_authorization(api_auth, normalize_tenant_id(req.tenant_id))
    set_audit_actor(api_auth)
    enforce_role(api_auth, ROLE_USER)
    feedback_token = str(req.feedback_token or "").strip()
    feedback_type = str(req.feedback_type or "").strip()
    if not feedback_token:
        raise HTTPException(status_code=400, detail="feedback_token is required")
    if feedback_type not in _ALLOWED_FEEDBACK_TYPES:
        raise HTTPException(status_code=400, detail="invalid feedback_type")

    # Observability only: feedback_type is a stable enum label (good / bad /
    # neutral / human_review_requested); never a raw key, query, or document.
    metrics_registry.increment("chat_feedback_total", feedback_type)

    event = _feedback_audit_event(
        req,
        feedback_token=feedback_token,
        feedback_type=feedback_type,
    )
    stored = append_feedback_audit_event(event)
    response: Dict[str, Any] = {
        "ok": True,
        "feedback_token": event["feedback_token"],
        "feedback_type": feedback_type,
        "stored": stored,
    }
    if not stored:
        response["warning"] = "feedback_logging_failed"
    return response


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


@app.get("/chat/suggest")
def chat_suggest(
    q: str = Query("", max_length=200),
    tenant_id: Optional[str] = None,
    limit: int = Query(5, ge=1, le=10),
    api_auth: ApiAuthContext = Depends(require_api_auth_rate_limited),
):
    # Questions of the caller's approved QA that match what is being typed. No answers, and the typed text is
    # neither logged nor audited.
    tenant = normalize_tenant_id(tenant_id)
    enforce_tenant_authorization(api_auth, tenant)
    index = _approved_qa_index_for(tenant)
    if index is None:
        return {"suggestions": []}
    return {"suggestions": question_suggest.suggest(q, index=index, tenant_id=tenant, limit=limit)}


@app.get("/chat/source")
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


@app.get("/chat/threads")
def list_chat_threads(api_auth: ApiAuthContext = Depends(require_api_auth_rate_limited), tenant_id: Optional[str] = None):
    _require_conversation_history()
    tenant = normalize_tenant_id(tenant_id)
    enforce_tenant_authorization(api_auth, tenant)
    set_audit_actor(api_auth)
    return {"threads": conversation_store.list_threads(tenant, _conversation_identity(api_auth))}


@app.get("/chat/threads/{thread_id}")
def get_chat_thread(thread_id: str, api_auth: ApiAuthContext = Depends(require_api_auth_rate_limited), tenant_id: Optional[str] = None):
    _require_conversation_history()
    tenant = normalize_tenant_id(tenant_id)
    enforce_tenant_authorization(api_auth, tenant)
    set_audit_actor(api_auth)
    return {"thread_id": thread_id, "turns": conversation_store.get_thread(tenant, _conversation_identity(api_auth), thread_id)}


@app.post("/chat/threads")
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


@app.delete("/chat/threads/{thread_id}")
def delete_chat_thread(thread_id: str, api_auth: ApiAuthContext = Depends(require_api_auth_rate_limited), tenant_id: Optional[str] = None):
    _require_conversation_history()
    tenant = normalize_tenant_id(tenant_id)
    enforce_tenant_authorization(api_auth, tenant)
    set_audit_actor(api_auth)
    enforce_role(api_auth, ROLE_USER)
    return {"ok": True, "deleted": conversation_store.delete_thread(tenant, _conversation_identity(api_auth), thread_id)}


@app.post("/search")
def search(req: SearchRequest, api_auth: ApiAuthContext = Depends(require_api_auth_rate_limited)):
    global _total_requests, _error_requests
    _total_requests += 1
    metrics_registry.note_request()
    tenant_id = normalize_tenant_id(req.tenant_id)
    enforce_tenant_authorization(api_auth, tenant_id)
    set_audit_actor(api_auth)
    staging_collection = _resolve_staging_collection(req.staging_collection, tenant_id=tenant_id)
    try:
        top_k = req.top_k or config.TOP_K

        # Approved QA exact lookup:
        # 確定QAと質問が完全一致する場合は、vector検索より前に必ず返す。
        # staging_collection指定時はステージング検証を優先するため exact bypass はしない。
        if not staging_collection and getattr(config, "APPROVED_QA_ENABLED", False):
            exact_hits = lookup_approved_qa_exact(req.query, tenant_id=tenant_id, limit=top_k)
            if exact_hits:
                return {
                    **_collection_meta(staging_collection),
                    "tenant_id": tenant_id,
                    "hits": exact_hits[:top_k],
                }

        client = _embedding_client()
        hits = retrieve_chunks(
            req.query,
            client=client,
            top_k=top_k,
            tenant_id=tenant_id,
            collection_name=staging_collection,
            create_collection_if_missing=not bool(staging_collection),
        )
        return {
            **_collection_meta(staging_collection),
            "tenant_id": tenant_id,
            "hits": [
                {"text": h.text, "metadata": h.metadata, "score": h.score}
                for h in hits
            ]
        }
    except Exception:
        _error_requests += 1
        metrics_registry.note_error()
        logging.exception("search failed trace_id=%s", req.trace_id)
        raise HTTPException(status_code=500, detail="internal error")


@app.post("/search/debug")
def search_debug(req: SearchDebugRequest, api_auth: ApiAuthContext = Depends(require_search_debug_access)):
    global _total_requests, _error_requests
    _total_requests += 1
    metrics_registry.note_request()
    tenant_id = normalize_tenant_id(req.tenant_id)
    enforce_tenant_authorization(api_auth, tenant_id)
    set_audit_actor(api_auth)
    staging_collection = _resolve_staging_collection(req.staging_collection, tenant_id=tenant_id)
    try:
        client = _embedding_client()
        ans = None
        if req.generate_answer:
            ans, trace = answer_query_with_trace(
                req.query,
                client=client,
                top_k=req.top_k or config.TOP_K,
                max_context_chars=req.max_context_chars or config.MAX_CONTEXT_CHARS,
                tenant_id=tenant_id,
                collection_name=staging_collection,
            )
        else:
            trace = debug_retrieve_with_trace(
                req.query,
                client=client,
                top_k=req.top_k or config.TOP_K,
                max_context_chars=req.max_context_chars or config.MAX_CONTEXT_CHARS,
                tenant_id=tenant_id,
                collection_name=staging_collection,
            )
        trace_id = req.trace_id or str(trace.get("request_id") or "")
        max_preview_chars = 1200 if req.include_context else 300
        before_rerank = _compact_chunks(
            trace.get("before_rerank"),
            max_preview_chars=max_preview_chars,
        )
        after_rerank = _compact_chunks(
            trace.get("after_rerank"),
            max_preview_chars=max_preview_chars,
        )
        after_parent_expansion = _compact_chunks(
            trace.get("after_parent_expansion"),
            max_preview_chars=max_preview_chars,
        )
        include_approved_similar = bool(
            req.include_approved_similar_candidates
            or getattr(config, "APPROVED_SIMILAR_CANDIDATES_ENABLED", False)
        )
        approved_exact = _approved_qa_lookup(req.query, tenant_id=tenant_id) if include_approved_similar else None
        approved_similar_candidates = []
        if include_approved_similar and approved_exact is None:
            approved_similar_candidates = search_approved_similar_candidates(
                req.query,
                client=client,
                tenant_id=tenant_id,
                top_k=req.approved_similar_top_k
                or getattr(config, "APPROVED_SIMILAR_CANDIDATES_TOP_K", 5),
            )
        response = {
            "request_id": trace.get("request_id"),
            "trace_id": trace_id,
            "original_query": trace.get("original_query") or req.query,
            "normalized_query": trace.get("normalized_query"),
            "intent": trace.get("intent") or (ans.intent if ans is not None else None),
            "query_type": trace.get("query_type"),
            "rewritten_query": trace.get("rewritten_query")
            or (ans.rewritten_query if ans is not None else None),
            "augmented_query": trace.get("augmented_query")
            or (ans.augmented_query if ans is not None else None),
            "before_rerank": before_rerank,
            "after_rerank": after_rerank,
            "after_parent_expansion": after_parent_expansion,
            "selected_context_chunk_ids": _trace_value(trace, "selected_context_chunk_ids", []),
            "selected_context_preview": _trace_value(trace, "selected_context_preview", []),
            "guard_reason": trace.get("final_guard_reason")
            or (ans.guard_reason if ans is not None else None),
            "used_fallback": _trace_value(
                trace,
                "final_used_fallback",
                ans.used_fallback if ans is not None else False,
            ),
            "answer_mode": trace.get("answer_mode"),
            "citations_count": _trace_value(
                trace,
                "citations_count",
                len(ans.citations) if ans is not None else 0,
            ),
            "approved_exact_match_found": approved_exact is not None if include_approved_similar else False,
            "approved_similar_candidates": approved_similar_candidates,
            "latency_ms": trace.get("latency_ms"),
            **_collection_meta(staging_collection),
            "tenant_id": tenant_id,
        }
        append_audit_event(
            "search_debug",
            {
                "request_id": trace.get("request_id"),
                "trace_id": trace_id,
                "query": req.query,
                "normalized_query": trace.get("normalized_query"),
                "intent": trace.get("intent") or (ans.intent if ans is not None else None),
                "query_type": response["query_type"],
                "answer_mode": response["answer_mode"],
                "guard_reason": response["guard_reason"],
                "used_fallback": response["used_fallback"],
                "before_rerank_count": len(before_rerank),
                "after_rerank_count": len(after_rerank),
                "after_parent_expansion_count": len(after_parent_expansion),
                "citations_count": response["citations_count"],
                "approved_similar_candidate_count": len(approved_similar_candidates),
                "tenant_id": tenant_id,
                "top_approved_similar_qa_id": (
                    approved_similar_candidates[0].get("qa_id")
                    if approved_similar_candidates
                    else None
                ),
                "latency_ms": response["latency_ms"],
                **_top_score_detail_summary(trace.get("after_rerank")),
            },
        )
        _record_chat_outcome_metrics(
            answer_mode=response["answer_mode"],
            guard_reason=response["guard_reason"],
            used_fallback=bool(response["used_fallback"]),
        )
        return response
    except Exception as exc:
        _error_requests += 1
        metrics_registry.note_error()
        generation_error = _generation_error_payload(exc) if req.generate_answer else None
        append_audit_event(
            "search_debug",
            {
                "request_id": None,
                "trace_id": req.trace_id,
                "query": req.query,
                "error": "chat generation unavailable" if generation_error else "internal error",
                "error_type": generation_error[1].get("error_type") if generation_error else None,
            },
        )
        logging.exception("search_debug failed trace_id=%s", req.trace_id)
        _record_provider_error_metric(generation_error)
        if generation_error:
            return JSONResponse(status_code=generation_error[0], content=generation_error[1])
        raise HTTPException(status_code=500, detail="internal error")
