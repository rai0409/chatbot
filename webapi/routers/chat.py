"""The chat core: /chat, /chat/stream, /chat/product-preview, /chat/feedback, /chat/suggest, /search and /search/debug
with their request models and helpers (moved from main.py, S7)."""
from __future__ import annotations

import contextvars
import json
import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, validator

import config
from rag_core import answer_cache
from rag_core import answer_evidence
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
    ANSWER_MODE_APPROVED_COLLOQUIAL_MATCH,
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
    verify_feedback_token,
)
from rag_core.feedback_rerank_profile import PROFILE_PATH, apply_feedback_preview_rerank
from rag_core.product_profile import load_product_profile
from rag_core.product_route_policy import build_route_policy
from rag_core import search_mode
from rag_core import suggest as question_suggest
from rag_core.profile_loader import is_valid_rag_profile_id
from rag_core.qa import (
    answer_query_stream,
    answer_query_with_trace,
    debug_retrieve_with_trace,
    retrieve_chunks,
)
from rag_core.question_normalization import normalize_question_for_exact_match
from rag_core.retrieval import RetrievedChunk, normalize_tenant_id
from rag_core.source_metadata import normalize_citation
from rag_core.tenant_profile import resolve_tenant_product_profile, tenant_chat_policy
from rag_core.utils import ensure_openai_client
from webapi import conversation_store
from webapi import metrics_registry
from webapi.api_auth import (
    ApiAuthContext,
    _parse_tenant_map,
    enforce_tenant_authorization,
    require_search_debug_access,
)
from webapi.rate_limit import require_api_auth_rate_limited
from webapi.rbac import ROLE_OPERATOR, ROLE_USER, caller_has_role, enforce_role
from webapi.routers.conversations import _conversation_identity
from webapi import request_stats
from webapi.chat_support import (
    _bounded_id_list,
    _bounded_text,
    _collection_meta,
    _preview,
    _resolve_staging_collection,
)
from webapi import records_hook
from rag_core import conversation_context
from rag_core.audit_log import set_audit_actor


router = APIRouter()
_ANSWER_CACHE_NAMESPACE = ("profile-validation-v1", "mail-quarantine-v2")

# Approved-QA indexes by (file path, tenant, file version). One dict under one
# lock: the former pair of module globals was written in two steps, so a
# request in another thread could read the index another tenant had just loaded.
_approved_qa_cache: Dict[tuple, ApprovedQAIndex] = {}
_approved_qa_cache_lock = threading.Lock()
_ALLOWED_FEEDBACK_TYPES = {
    "good",
    "bad",
    "neutral",
    "human_review_requested",
}


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

    @validator("thread_id")
    def _validate_thread_id(cls, value):
        if value is not None:
            from webapi.routers.conversations import validate_thread_id

            try:
                validate_thread_id(value)
            except HTTPException as exc:
                raise ValueError("invalid thread_id") from exc
        return value


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
    if isinstance(exc, LlmNotAllowedForTenant):
        return 503, {
            "detail": "chat generation unavailable",
            "error_type": "llm_not_allowed_for_tenant",
        }
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
    answer = lookup_approved_answer(index, question, tenant_id=normalize_tenant_id(tenant_id))
    if answer is not None and not _approved_answer_sources_exist(answer):
        metrics_registry.increment("chat_approved_dropped_total", "source_missing")
        return None
    return answer


def _approved_qa_index_for(tenant_id: str = "default"):
    """The approved-QA index of a tenant (cached), or None when approved QA is off."""
    if not getattr(config, "APPROVED_QA_ENABLED", False):
        return None
    tenant = normalize_tenant_id(tenant_id)
    path = str(config.APPROVED_QA_PATH)
    # The file's version (mtime, size) is part of the key: an edited approved-QA
    # file is reloaded on the next request instead of after a restart.
    try:
        stat = os.stat(path)
        version = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        version = None
    key = (path, tenant, version)
    with _approved_qa_cache_lock:
        index = _approved_qa_cache.get(key)
        if index is not None:
            return index
        # load_approved_qa filters records per tenant, so an index is only ever
        # served for the tenant it was loaded for (the tenant is in the key).
        index = load_approved_qa(path, tenant_id=tenant)
        # Older versions of this file are no longer reachable: drop them.
        for old in [k for k in _approved_qa_cache if k[0] == path and k[1] == tenant]:
            del _approved_qa_cache[old]
        _approved_qa_cache[key] = index
        return index


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
    answer_mode = _approved_answer_mode(answer)
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
        "retrieval_source": {
            ANSWER_MODE_APPROVED_EXACT_MATCH: "approved_qa_exact",
            ANSWER_MODE_APPROVED_ALIAS_MATCH: "approved_qa_alias",
            ANSWER_MODE_APPROVED_COLLOQUIAL_MATCH: "approved_qa_colloquial",
        }[answer_mode],
        "retrieval_required": False,
        "llm_used": False,
    }


def _approved_answer_mode(answer: ApprovedAnswer) -> str:
    if answer.match_type == "canonical":
        return ANSWER_MODE_APPROVED_EXACT_MATCH
    if answer.match_type == "alias":
        return ANSWER_MODE_APPROVED_ALIAS_MATCH
    if answer.match_type == "colloquial":
        return ANSWER_MODE_APPROVED_COLLOQUIAL_MATCH
    raise ValueError(f"unsupported approved QA match_type: {answer.match_type}")


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


# index object -> (index, {qa_id: record}, frozenset(qa_id)); the approved-QA index is cached per file version
# (_approved_qa_index_for), so this view is built once per version instead of once per request.
_LIVE_VIEW_CACHE: Dict[int, tuple] = {}
_LIVE_VIEW_CACHE_MAX = 64
_LIVE_VIEW_LOCK = threading.Lock()


def _live_view(index) -> tuple:
    key = id(index)
    with _LIVE_VIEW_LOCK:
        entry = _LIVE_VIEW_CACHE.get(key)
        if entry is not None and entry[0] is index:  # `is`: a reused id() of a collected index never matches
            return entry
        records = {record.qa_id: record for record in index.records}
        entry = (index, records, frozenset(records))
        if len(_LIVE_VIEW_CACHE) >= _LIVE_VIEW_CACHE_MAX:
            _LIVE_VIEW_CACHE.clear()
        _LIVE_VIEW_CACHE[key] = entry
        return entry


def _live_approved_validator(tenant_id: str):
    """hit -> bool: the exact-index hit is still an approved record of the live approved-QA file, with the same
    answer (an edit returns a record to draft; a re-approved edit changes the answer). Fail closed: without a live
    index nothing passes."""
    index = _approved_qa_index_for(tenant_id)
    live = _live_view(index)[1] if index is not None else {}

    def check(hit: dict) -> bool:
        meta = hit.get("metadata") or {}
        qa_id = str(meta.get("qa_id") or meta.get("approved_qa_id") or "").strip()
        record = live.get(qa_id)
        if record is None:
            return False
        answer = str(meta.get("approved_answer") or meta.get("answer_text") or hit.get("answer_text") or "").strip()
        return answer == str(record.approved_answer or "").strip()

    return check


def _live_approved_qa_ids(tenant_id: str):
    """qa_ids approved right now for the tenant, or None when approved QA is off (nothing to check against)."""
    index = _approved_qa_index_for(tenant_id)
    if index is None:
        return None
    return _live_view(index)[2]


def _live_similar_kwargs(tenant_id: str) -> Dict[str, Any]:
    """live_qa_ids for the similar-candidate search when the live approved-QA file is on (rows of records that
    were rejected, edited back to draft or deleted after ingest are dropped); nothing otherwise."""
    live = _live_approved_qa_ids(tenant_id)
    return {} if live is None else {"live_qa_ids": live}


def _approved_answer_sources_exist(answer: ApprovedAnswer) -> bool:
    from rag_core.approved_qa_exact_lookup import cited_sources_exist

    return cited_sources_exist(answer.tenant_id, [c.source_doc for c in answer.approved_citations if c.source_doc])


def _approved_exact_chat_lookup(question: str, tenant_id: str) -> Optional[Dict[str, Any]]:
    if not getattr(config, "APPROVED_QA_ENABLED", False):
        return None
    hits = lookup_approved_qa_exact(question, tenant_id=tenant_id, limit=1, live_validator=_live_approved_validator(tenant_id))
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


_STAGE_LATENCY_BUCKETS_MS = (250, 500, 1000, 2000, 4000, 8000)


def _record_stage_latency_metrics(trace, *, citations_count=None, guard_reason=None) -> None:
    """Commercial fix F11: per-stage latency as cumulative buckets and the no-citation count.

    chat_stage_latency_bucket{label="<stage>:le<ms>"} / "<stage>:le+Inf" and
    chat_stage_latency_count{label="<stage>"} give p50/p95 per stage on /metrics;
    chat_no_citation_total counts answers that were not guarded yet cite nothing
    (an accuracy-regression signal).
    """
    if not isinstance(trace, dict):
        return
    stages = dict(trace.get("stage_latency_ms") or {})
    if trace.get("latency_ms") is not None:
        stages["total_ms"] = trace.get("latency_ms")
    for stage, value in stages.items():
        try:
            ms = float(value)
        except (TypeError, ValueError):
            continue
        name = str(stage)
        if name.endswith("_ms"):
            name = name[:-3]
        metrics_registry.increment("chat_stage_latency_count", name)
        for le in _STAGE_LATENCY_BUCKETS_MS:
            if ms <= le:
                metrics_registry.increment("chat_stage_latency_bucket", f"{name}:le{le}")
        metrics_registry.increment("chat_stage_latency_bucket", f"{name}:le+Inf")
    if citations_count is not None and not guard_reason and int(citations_count) == 0:
        metrics_registry.increment("chat_no_citation_total")


def _record_provider_error_metric(generation_error) -> None:
    if generation_error:
        metrics_registry.increment(
            "chat_provider_error_total",
            str(generation_error[1].get("error_type") or "unknown"),
        )



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




# --- /chat answer policy (2026-11) -------------------------------------------------------------------------------
# The tenant's answer policy is enforced on /chat, /chat/stream and /search WITHOUT an opt-in flag:
#   * tenant status disabled, or an unknown tenant under unknown_tenant_policy=reject  -> 403
#   * answer_policy.allow_exact_answer=false (product profile, or the tenant's chat_policy) -> no approved-QA answer
#   * human_review_on_low_confidence=true -> a guarded / fallback / unvalidated answer is marked review_required
#   * allow_llm_calls=false in the tenant's chat_policy -> extractive answer, no LLM call
# CHAT_USE_TENANT_PROFILE (default false) still gates what it always gated (the candidate clamp) and, in addition,
# the product profile's cost_policy.allow_llm_calls: every shipped product profile says false (they were written for
# the approved-similar preview), so enforcing it unconditionally would silently switch LLM generation off for every
# tenant of an LLM deployment. A tenant that must never reach an LLM gets "chat_policy": {"allow_llm_calls": false}
# in configs/product_tenants/default.json, which is enforced regardless of the flag. See docs/operations/profile_engine.md.
_POLICY_DEFAULTS: Dict[str, Any] = {
    "allow_exact_answer": True,
    "allow_llm_calls": True,
    "human_review_on_low_confidence": False,
    "fallback_to_no_answer": True,
}


def _profile_answer_policy(profile_name: str) -> Optional[Dict[str, Any]]:
    """(answer_policy, cost_policy) of a product profile that loads as itself, else None."""
    try:
        policy = build_route_policy(load_product_profile(profile_name))
    except Exception:  # noqa: BLE001  profile load failure is logged; no profile applied
        logging.exception("chat answer policy: product profile %s failed to load", profile_name)
        return None
    if str(policy.get("profile_name") or "") != profile_name:
        return None
    return {"answer_policy": dict(policy.get("answer_policy") or {}), "cost_policy": dict(policy.get("cost_policy") or {})}


def resolve_chat_answer_policy(tenant_id, *, customer_id=None) -> Dict[str, Any]:
    """The answer policy of a tenant for /chat (see the block comment above). Never raises.

    Fails CLOSED: a tenant mapping (or chat_policy block) that cannot be read blocks the request
    (block_reason policy_unavailable -> 503; logged and counted in chat_answer_policy_error_total), and a product
    profile that cannot be loaded turns approved answers off. A missing mapping file is not an error (the
    resolution falls back to the default tenant behaviour); a mapping that disables the tenant always blocks."""
    tenant = str(tenant_id or "").strip() or "default"
    policy: Dict[str, Any] = {**_POLICY_DEFAULTS, "blocked": False, "block_reason": None, "decision": None,
                              "tenant_status": None, "profile_name": None, "sources": []}
    try:
        resolution = resolve_tenant_product_profile(tenant, customer_id=customer_id, strict=False)
    except Exception:  # noqa: BLE001  fail closed: policy_unavailable blocks the answer, logged
        logging.exception("chat answer policy: tenant profile resolution failed")
        metrics_registry.increment("chat_answer_policy_error_total", "resolution")
        policy.update(decision="resolution_error", blocked=True, block_reason="policy_unavailable")
        return policy
    decision = str(resolution.get("decision") or "") or "resolved"
    policy["decision"] = decision
    policy["tenant_status"] = resolution.get("tenant_status")
    if decision in {"disabled", "rejected"}:
        policy["blocked"] = True
        policy["block_reason"] = "tenant_disabled" if decision == "disabled" else "tenant_rejected"
        return policy

    profile_name = str(resolution.get("resolved_profile") or resolution.get("default_profile") or _CHAT_SAFE_PROFILE).strip()
    loaded = _profile_answer_policy(profile_name)
    if loaded is None and profile_name != _CHAT_SAFE_PROFILE:
        profile_name = _CHAT_SAFE_PROFILE  # fail closed to the safest profile, as resolve_chat_runtime_profile does
        loaded = _profile_answer_policy(profile_name)
    if loaded is None:
        # No usable profile at all: nothing says exact answers are allowed (fail closed).
        metrics_registry.increment("chat_answer_policy_error_total", "profile")
        policy["allow_exact_answer"] = False
    else:
        answer, cost = loaded["answer_policy"], loaded["cost_policy"]
        policy["profile_name"] = profile_name
        policy["sources"].append("product_profile")
        policy["allow_exact_answer"] = answer.get("allow_exact_answer") is not False
        policy["human_review_on_low_confidence"] = answer.get("human_review_on_low_confidence") is True
        policy["fallback_to_no_answer"] = answer.get("fallback_to_no_answer") is not False
        if config.CHAT_USE_TENANT_PROFILE:
            policy["allow_llm_calls"] = cost.get("allow_llm_calls") is True
    try:
        explicit = tenant_chat_policy(tenant)
    except Exception:  # noqa: BLE001  fail closed: policy_unavailable blocks the answer, logged
        logging.exception("chat answer policy: tenant chat_policy unreadable")
        metrics_registry.increment("chat_answer_policy_error_total", "chat_policy")
        policy.update(blocked=True, block_reason="policy_unavailable")
        return policy
    if explicit:
        policy["sources"].append("tenant_chat_policy")
        # A tenant block narrows only: it can switch answers/LLM off and review on, never the reverse.
        if explicit.get("allow_exact_answer") is False:
            policy["allow_exact_answer"] = False
        if explicit.get("allow_llm_calls") is False:
            policy["allow_llm_calls"] = False
        if explicit.get("human_review_on_low_confidence") is True:
            policy["human_review_on_low_confidence"] = True
    return policy


def _enforce_chat_answer_policy(policy: Dict[str, Any], *, tenant_id: str, endpoint: str, req_question: str = "",
                                trace_id: Optional[str] = None) -> None:
    if not policy.get("blocked"):
        return
    reason = str(policy.get("block_reason") or "tenant_blocked")
    metrics_registry.increment("chat_tenant_blocked_total", reason)
    append_audit_event(endpoint, {
        "request_id": None, "trace_id": trace_id, "tenant_id": tenant_id, "question": req_question,
        "error": "tenant blocked by answer policy", "error_type": reason,
    })
    if reason == "policy_unavailable":
        raise HTTPException(status_code=503, detail="tenant policy unavailable")
    detail = "tenant is disabled" if reason == "tenant_disabled" else "tenant is not allowed"
    raise HTTPException(status_code=403, detail=detail)


def _llm_mode_for(tenant_id: str) -> str:
    try:
        from rag_core import llm

        return llm.generation_mode(tenant_id)
    except Exception:  # noqa: BLE001  only picks between llm / extractive; "llm" then fails closed in the caller
        logging.warning("generation mode of tenant could not be resolved; using the configured mode", exc_info=True)
        return config.resolve_chat_generation_mode()


_MISSING_HOOK_LOGGED = threading.Event()


def _log_missing_extractive_hook() -> None:
    if not _MISSING_HOOK_LOGGED.is_set():
        _MISSING_HOOK_LOGGED.set()
        logging.warning("rag_core.llm.extractive_only is not available: tenants with allow_llm_calls=false are "
                        "refused (503) instead of being answered extractively when the LLM mode is on")


class LlmNotAllowedForTenant(RuntimeError):
    """The tenant policy forbids LLM calls and this build cannot force an extractive answer per request."""


def _extractive_only_context(policy: Dict[str, Any], tenant_id: str):
    """A context-manager factory that keeps the request free of LLM calls when the policy says so.

    Uses rag_core.llm.extractive_only() (request-scoped) when available. Without it, a request that would use the
    LLM is refused (fail closed: LlmNotAllowedForTenant -> 503) rather than calling a model the tenant forbids."""
    from contextlib import nullcontext

    if policy.get("allow_llm_calls", True):
        return nullcontext
    from rag_core import llm

    # rag_core.llm has no request-scoped extractive_only() yet: say so once instead of falling back silently.
    forced = getattr(llm, "extractive_only", None)
    if callable(forced):
        return forced
    _log_missing_extractive_hook()
    if _llm_mode_for(tenant_id) == "llm":
        raise LlmNotAllowedForTenant("LLM calls are not allowed for this tenant")
    return nullcontext


def _iterate_in_context(context_factory, iterator):
    """Re-enter a context manager around each step of a generator (a streamed response runs each step in a
    worker thread with a fresh context, so a ContextVar set once would not stay set)."""
    while True:
        with context_factory():
            try:
                item = next(iterator)
            except StopIteration:
                return
        yield item


def _is_low_confidence(payload: Dict[str, Any]) -> bool:
    mode = str(payload.get("answer_mode") or "")
    if payload.get("guard_reason") or payload.get("used_fallback") or payload.get("abstained"):
        return True
    if "fallback" in mode or "no_answer" in mode:
        return True
    validation = payload.get("profile_validation")
    return isinstance(validation, dict) and validation.get("passed") is False and not validation.get("skipped")


def _mark_for_review(payload: Dict[str, Any], event: Optional[Dict[str, Any]], policy: Dict[str, Any]) -> bool:
    """human_review_on_low_confidence: mark the answer (response and audit event) for the review queue.

    The review queue is built offline from audit events (rag_core/review_queue.py), so the mark is the
    review_required / review_reason fields of the chat event."""
    if not policy.get("human_review_on_low_confidence") or not _is_low_confidence(payload):
        return False
    payload["review_required"] = True
    payload["review_reason"] = "low_confidence"
    if event is not None:
        event["review_required"] = True
        event["review_reason"] = "low_confidence"
    metrics_registry.increment("chat_review_required_total", "low_confidence")
    return True


def _approved_lookups_allowed(policy: Dict[str, Any], staging_collection: Optional[str]) -> bool:
    return not staging_collection and bool(policy.get("allow_exact_answer", True))


def _keyword_index_refreshing() -> bool:
    """True while the served keyword index is rebuilt in the background (the previous index is still answering,
    but the cache key already names the new corpus file): such an answer must not be cached under that key."""
    from rag_core.retrieval import keyword_index_refreshing

    try:
        return bool(keyword_index_refreshing())
    except Exception:  # noqa: BLE001  unknown refresh state: the answer is not cached, logged
        logging.warning("keyword index refresh state unknown; the answer is not cached", exc_info=True)
        return True  # unknown: do not cache


_CACHE_EXCLUDED_FIELDS = {"conversation_context", "trace_id", "request_id", "feedback_token"}


def _chat_cache_key(question: str, *, api_auth: Any, answer_policy: Dict[str, Any], top_k: int,
                    max_context_chars: int, tenant_id: str, rag_profile_id: str) -> Optional[tuple]:
    """The answer-cache key of a /chat request, or None when this answer must not use the cache.

    The caller's access scope is part of the key (answer_cache.access_scope_digest); a caller whose scope cannot be
    determined while retrieval is ACL-filtered is not cached (fail closed). The tenant answer policy's LLM switch is
    part of the key too, so a tenant switched to "no LLM" is not served answers an LLM wrote before."""
    scope = answer_cache.access_scope_digest(api_auth)
    if scope is None:
        metrics_registry.increment("chat_cache_skip_total", "access_scope_unknown")
        return None
    key = answer_cache.build_key(
        question, top_k=top_k, max_context_chars=max_context_chars, tenant_id=tenant_id,
        rag_profile_id=rag_profile_id, principal_digest=scope,
    )
    # The namespace stays the last elements of the key.
    return (*key, ("allow_llm_calls", bool(answer_policy.get("allow_llm_calls", True))), *_ANSWER_CACHE_NAMESPACE)


def _cache_entry(payload: Dict[str, Any], *, evidence: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    entry = {k: v for k, v in payload.items() if k not in _CACHE_EXCLUDED_FIELDS}
    if evidence is not None:
        entry[answer_cache.ORIGIN_FIELD] = answer_evidence.origin_for_cache(evidence)
    return entry


def _cache_hit_payload(cached: Dict[str, Any], *, request_id: str, trace_id: Optional[str],
                       conversation_trace: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    # The stored copy holds no per-request fields: this request's own ids and conversation context are added
    # back (stage 14); the internal origin is never sent.
    hit = answer_cache.strip_internal(dict(cached))
    hit["request_id"] = request_id
    if trace_id:
        hit["trace_id"] = trace_id
    if conversation_trace:
        hit["conversation_context"] = conversation_trace
    return hit


def _cache_origin(cached: Dict[str, Any]) -> Dict[str, Any]:
    origin = cached.get(answer_cache.ORIGIN_FIELD)
    return dict(origin) if isinstance(origin, dict) else {}


def _record_answer_evidence(request_id: str, tenant_id: str, payload: Dict[str, Any], *,
                            trace: Optional[Dict[str, Any]] = None, collection: Optional[str] = None,
                            cache_key: Optional[tuple] = None, served_from_cache: bool = False,
                            origin: Optional[Dict[str, Any]] = None, trace_id: Optional[str] = None,
                            streamed: bool = False, api_auth: Any = None) -> Optional[Dict[str, Any]]:
    if not answer_evidence.enabled():
        return None
    try:
        record = answer_evidence.build_evidence(
            request_id=request_id, tenant_id=tenant_id, payload=payload, trace=trace, collection=collection,
            cache_key_digest=answer_cache.key_digest(cache_key) if cache_key is not None else None,
            served_from_cache=served_from_cache, origin=origin, trace_id=trace_id, streamed=streamed,
            access_scope=answer_cache.access_scope_digest(api_auth),
        )
        return record if answer_evidence.record_evidence(record) else None
    except Exception:  # noqa: BLE001  evidence never breaks an answer; logged
        logging.exception("answer evidence not recorded request_id=%s", request_id)


def _server_request_id(trace: Optional[Dict[str, Any]]) -> str:
    # The pipeline's own id (server-generated) names the answer; a client trace_id never does.
    value = str((trace or {}).get("request_id") or "").strip()
    return value or answer_evidence.new_request_id()


def _run_in_captured_context(iterator):
    """Advance a streamed generator in ONE context captured now (after set_audit_actor).

    Starlette runs each next() of a sync generator in a worker thread with a fresh copy of the request context, so a
    ContextVar set in the endpoint (the audit actor used by quota, model selection and audit) was lost after the
    first step."""
    ctx = contextvars.copy_context()

    def run():
        while True:
            try:
                item = ctx.run(next, iterator)
            except StopIteration:
                return
            yield item

    return run()


def _cacheable(payload: Dict[str, Any], guard_reason: Any, used_fallback: Any) -> bool:
    if guard_reason is not None or used_fallback or not _cache_validation_passed(payload):
        return False
    if payload.get("review_required"):
        return False
    if _keyword_index_refreshing():
        metrics_registry.increment("chat_cache_skip_total", "keyword_index_refreshing")
        return False
    return True


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


@router.post("/chat")
def chat(req: ChatRequest, api_auth: ApiAuthContext = Depends(require_api_auth_rate_limited), request: Request = None):
    request_stats.total_requests += 1
    metrics_registry.note_request()
    tenant_id = normalize_tenant_id(req.tenant_id)
    enforce_tenant_authorization(api_auth, tenant_id)
    set_audit_actor(api_auth)
    answer_policy = resolve_chat_answer_policy(tenant_id)
    _enforce_chat_answer_policy(answer_policy, tenant_id=tenant_id, endpoint="chat", req_question=req.question,
                                trace_id=req.trace_id)
    staging_collection = _resolve_staging_collection(req.staging_collection, tenant_id=tenant_id)
    rag_profile_id = _resolve_rag_profile_id(req, staging_collection, tenant_id)
    question_for_answer, conversation_trace = _conversation_question(req, api_auth, tenant_id)
    # Approved answers are looked up with the question that is answered: a follow-up completed from the
    # conversation (申請方法は？ -> 有給 申請方法は？) must not hit an unrelated approved QA by its bare wording.
    approved_allowed = _approved_lookups_allowed(answer_policy, staging_collection)
    try:
        approved = _approved_qa_lookup(question_for_answer, tenant_id=tenant_id) if approved_allowed else None
        if approved is not None:
            payload = _approved_chat_payload(approved)
            approved_mode = _approved_answer_mode(approved)
            approved_request_id = answer_evidence.new_request_id()
            payload["request_id"] = approved_request_id
            payload["trace_id"] = req.trace_id or approved_request_id
            append_audit_event(
                "chat",
                {
                    "request_id": approved_request_id,
                    "trace_id": payload["trace_id"],
                    "tenant_id": approved.tenant_id,
                    "question": req.question,
                    **_approved_audit_fields(approved, req.question),
                    "cited_source_docs": answer_evidence.cited_source_docs(payload.get("citations")),
                },
            )
            _record_answer_evidence(approved_request_id, tenant_id, payload, trace_id=req.trace_id, api_auth=api_auth)
            _record_chat_outcome_metrics(answer_mode=approved_mode)
            return payload

        approved_exact_payload = (
            _approved_exact_chat_lookup(question_for_answer, tenant_id) if approved_allowed else None
        )
        if approved_exact_payload is not None:
            approved_exact_payload.update(_collection_meta(staging_collection))
            exact_request_id = answer_evidence.new_request_id()
            approved_exact_payload["request_id"] = exact_request_id
            approved_exact_payload["trace_id"] = req.trace_id or approved_exact_payload.get("trace_id") or exact_request_id
            append_audit_event(
                "chat",
                {
                    "request_id": exact_request_id,
                    "trace_id": req.trace_id,
                    "tenant_id": tenant_id,
                    "question": req.question,
                    "normalized_question": approved_exact_payload.get("normalized_question"),
                    "answer_mode": "approved_exact_match",
                    "approved_qa_id": approved_exact_payload.get("approved_qa_id"),
                    "retrieval_source": "approved_qa_exact",
                    "citations_count": len(approved_exact_payload.get("citations") or []),
                    "cited_source_docs": answer_evidence.cited_source_docs(approved_exact_payload.get("citations")),
                },
            )
            _record_answer_evidence(exact_request_id, tenant_id, approved_exact_payload, trace_id=req.trace_id, api_auth=api_auth)
            _record_chat_outcome_metrics(answer_mode="approved_exact_match")
            return approved_exact_payload

        runtime_profile = resolve_chat_runtime_profile(tenant_id)
        effective_top_k = _apply_chat_profile_top_k(req.top_k or config.TOP_K, runtime_profile)
        effective_max_context_chars = req.max_context_chars or config.MAX_CONTEXT_CHARS
        _record_chat_profile_metric(runtime_profile)
        if req.deep:
            effective_top_k = min(effective_top_k * 2, _MAX_TOP_K)
        cache_key = None
        # Any rag profile is cacheable: its files are part of the key (answer_cache._profile_state).
        if answer_cache.enabled() and not staging_collection and not req.deep:
            cache_key = _chat_cache_key(
                question_for_answer, api_auth=api_auth, answer_policy=answer_policy, top_k=effective_top_k,
                max_context_chars=effective_max_context_chars, tenant_id=tenant_id, rag_profile_id=rag_profile_id,
            )
            cached = answer_cache.get(cache_key) if cache_key is not None else None
            if cached is not None and (not _cache_validation_passed(cached) or
                    (answer_evidence.enabled() and not answer_evidence.valid_cache_origin(
                        _cache_origin(cached), payload=cached, tenant_id=tenant_id,
                        cache_key_digest=answer_cache.key_digest(cache_key), access_scope=answer_cache.access_scope_digest(api_auth)))):
                cached = None
            if cached is not None:
                # A cache hit is a new answer event: a new request id, with the origin request referenced.
                hit_request_id = answer_evidence.new_request_id()
                origin = _cache_origin(cached)
                hit = _cache_hit_payload(cached, request_id=hit_request_id, trace_id=req.trace_id,
                                         conversation_trace=conversation_trace)
                append_audit_event(
                    "chat",
                    {
                        "request_id": hit_request_id,
                        "trace_id": req.trace_id,
                        "tenant_id": tenant_id,
                        "question": req.question,
                        "guard_reason": cached.get("guard_reason"),
                        "used_fallback": cached.get("used_fallback"),
                        "citations_count": len(cached.get("citations") or []),
                        "cited_source_docs": answer_evidence.cited_source_docs(hit.get("citations")),
                        "cache_hit": True,
                        "origin_request_id": origin.get("request_id"),
                    },
                )
                _record_answer_evidence(hit_request_id, tenant_id, hit, cache_key=cache_key, served_from_cache=True,
                                        origin=origin, trace_id=req.trace_id, api_auth=api_auth)
                metrics_registry.increment("chat_cache_hit_total")
                # The stored payload carries its answer mode; a cached
                # fallback is not counted as a grounded answer.
                _record_chat_outcome_metrics(answer_mode=str(cached.get("answer_mode") or "grounded"))
                records_hook.record_chat(api_auth, request, tenant_id=tenant_id, question=req.question, payload=hit,
                                         request_id=hit_request_id, trace_id=req.trace_id, source="cache")
                return hit

        llm_context = _extractive_only_context(answer_policy, tenant_id)
        client = (
            ensure_openai_client(base_url=config.OPENAI_BASE_URL)
            if config.resolve_chat_generation_mode() == "llm" and answer_policy.get("allow_llm_calls", True)
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
        with search_mode.deep_mode(bool(req.deep)), llm_context():
            ans, trace = answer_query_with_trace(question_for_answer, **answer_kwargs)
        request_id = _server_request_id(trace)
        chat_event = {
            "request_id": request_id,
            "trace_id": req.trace_id or request_id,
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
        if not answer_policy.get("allow_llm_calls", True):
            chat_event["llm_allowed"] = False
        _record_stage_latency_metrics(trace, citations_count=len(ans.citations or []), guard_reason=ans.guard_reason)
        _record_chat_outcome_metrics(
            answer_mode=trace.get("answer_mode")
            or ("fallback" if (ans.guard_reason or ans.used_fallback) else "grounded"),
            guard_reason=trace.get("final_guard_reason") or ans.guard_reason,
            used_fallback=bool(trace.get("final_used_fallback", ans.used_fallback)),
        )
        payload = ans.to_dict()
        payload.update(_collection_meta(staging_collection))
        payload["request_id"] = request_id
        if req.trace_id:
            payload["trace_id"] = req.trace_id
        # The documents actually cited (top_source_docs above are the post-rerank candidates).
        chat_event["cited_source_docs"] = answer_evidence.cited_source_docs(payload.get("citations"))
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
        # The screen says "the AI is unavailable, this is the text of the documents" for this answer (S3/S5).
        payload["service_notice"] = records_hook.service_notice(trace)
        payload["profile_validation"] = _profile_validation_or_skipped(trace)
        _mark_for_review(payload, chat_event, answer_policy)
        append_audit_event("chat", chat_event)
        evidence = _record_answer_evidence(request_id, tenant_id, payload, trace=trace, collection=staging_collection,
                                           cache_key=cache_key, trace_id=req.trace_id, api_auth=api_auth)
        if cache_key is not None and (not answer_evidence.enabled() or evidence is not None) and _cacheable(payload, ans.guard_reason, ans.used_fallback):
            # Only clean grounded answers are cacheable; guard/no-answer and
            # extractive-fallback responses are never cached.
            # Stage 14: without the caller's conversation context (it holds that
            # user's earlier question) or request ids; another caller in the
            # tenant asking the same wording must not see them. The internal
            # origin (request, generation, key digest) is kept for provenance.
            answer_cache.put(cache_key, _cache_entry(payload, evidence=evidence))
        records_hook.record_chat(api_auth, request, tenant_id=tenant_id, question=req.question, payload=payload, trace=trace,
                                 request_id=request_id, trace_id=req.trace_id)
        return payload
    except Exception as exc:
        request_stats.error_requests += 1
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


def _profile_validation_or_skipped(trace: Dict[str, Any]) -> Dict[str, Any]:
    return trace.get("profile_validation") or {
        "passed": False, "skipped": True, "matched_rule_id": "",
        "question_type": trace.get("question_type", "other"), "missing_any": [],
        "missing_all": [], "forbidden_hits": [], "fallback_if_failed": False,
        "reason": "profile validation trace unavailable",
    }


def _cache_validation_passed(payload: Dict[str, Any]) -> bool:
    validation = payload.get("profile_validation")
    return isinstance(validation, dict) and validation.get("passed") is True and not validation.get("skipped")


@router.post("/chat/stream")
def chat_stream(req: ChatRequest, api_auth: ApiAuthContext = Depends(require_api_auth_rate_limited), request: Request = None):
    request_stats.total_requests += 1
    metrics_registry.note_request()
    tenant_id = normalize_tenant_id(req.tenant_id)
    # Raise before the StreamingResponse is constructed so an unauthorized
    # tenant gets a plain 403 and no stream ever starts.
    enforce_tenant_authorization(api_auth, tenant_id)
    set_audit_actor(api_auth)
    # The tenant answer policy of /chat; a blocked tenant gets a plain 403 before the stream starts.
    answer_policy = resolve_chat_answer_policy(tenant_id)
    _enforce_chat_answer_policy(answer_policy, tenant_id=tenant_id, endpoint="chat", req_question=req.question,
                                trace_id=req.trace_id)
    staging_collection = _resolve_staging_collection(req.staging_collection, tenant_id=tenant_id)
    # Stage 14: the same profile resolution as /chat (400/403 before the stream starts).
    rag_profile_id = _resolve_rag_profile_id(req, staging_collection, tenant_id)
    question_for_answer, conversation_trace = _conversation_question(req, api_auth, tenant_id)
    approved_allowed = _approved_lookups_allowed(answer_policy, staging_collection)

    def _events():
        set_audit_actor(api_auth)
        try:
            approved = _approved_qa_lookup(question_for_answer, tenant_id=tenant_id) if approved_allowed else None
            if approved is not None:
                payload = _approved_chat_payload(approved)
                approved_mode = _approved_answer_mode(approved)
                approved_request_id = answer_evidence.new_request_id()
                payload["request_id"] = approved_request_id
                payload["trace_id"] = req.trace_id or approved_request_id
                append_audit_event(
                    "chat",
                    {
                        "request_id": approved_request_id,
                        "trace_id": payload["trace_id"],
                        "tenant_id": approved.tenant_id,
                        "question": req.question,
                        **_approved_audit_fields(approved, req.question),
                        "cited_source_docs": answer_evidence.cited_source_docs(payload.get("citations")),
                    },
                )
                _record_answer_evidence(approved_request_id, tenant_id, payload, trace_id=req.trace_id, streamed=True, api_auth=api_auth)
                _record_chat_outcome_metrics(answer_mode=approved_mode)
                yield _sse_event("approved", payload)
                return

            approved_exact_payload = (
                _approved_exact_chat_lookup(question_for_answer, tenant_id) if approved_allowed else None
            )
            if approved_exact_payload is not None:
                approved_exact_payload.update(_collection_meta(staging_collection))
                exact_request_id = answer_evidence.new_request_id()
                approved_exact_payload["request_id"] = exact_request_id
                approved_exact_payload["trace_id"] = (
                    req.trace_id or approved_exact_payload.get("trace_id") or exact_request_id
                )
                _record_answer_evidence(exact_request_id, tenant_id, approved_exact_payload, trace_id=req.trace_id,
                                        streamed=True, api_auth=api_auth)
                append_audit_event(
                    "chat",
                    {
                        "request_id": exact_request_id,
                        "trace_id": req.trace_id,
                        "tenant_id": tenant_id,
                        "question": req.question,
                        "normalized_question": approved_exact_payload.get("normalized_question"),
                        "answer_mode": "approved_exact_match",
                        "approved_qa_id": approved_exact_payload.get("approved_qa_id"),
                        "retrieval_source": "approved_qa_exact",
                        "citations_count": len(approved_exact_payload.get("citations") or []),
                        "cited_source_docs": answer_evidence.cited_source_docs(approved_exact_payload.get("citations")),
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
            if answer_cache.enabled() and not staging_collection and not req.deep:
                cache_key = _chat_cache_key(
                    question_for_answer, api_auth=api_auth, answer_policy=answer_policy, top_k=effective_top_k,
                    max_context_chars=effective_max_context_chars, tenant_id=tenant_id, rag_profile_id=rag_profile_id,
                )
                cached = answer_cache.get(cache_key) if cache_key is not None else None
                if cached is not None and (not _cache_validation_passed(cached) or
                        (answer_evidence.enabled() and not answer_evidence.valid_cache_origin(
                            _cache_origin(cached), payload=cached, tenant_id=tenant_id,
                            cache_key_digest=answer_cache.key_digest(cache_key), access_scope=answer_cache.access_scope_digest(api_auth)))):
                    cached = None
                if cached is not None:
                    hit_request_id = answer_evidence.new_request_id()
                    origin = _cache_origin(cached)
                    hit = _cache_hit_payload(cached, request_id=hit_request_id, trace_id=req.trace_id,
                                             conversation_trace=conversation_trace)
                    append_audit_event("chat", {
                        "request_id": hit_request_id, "trace_id": req.trace_id, "tenant_id": tenant_id,
                        "question": req.question, "guard_reason": hit.get("guard_reason"),
                        "used_fallback": hit.get("used_fallback"),
                        "citations_count": len(hit.get("citations") or []),
                        "cited_source_docs": answer_evidence.cited_source_docs(hit.get("citations")),
                        "cache_hit": True, "streamed": True, "origin_request_id": origin.get("request_id"),
                    })
                    _record_answer_evidence(hit_request_id, tenant_id, hit, cache_key=cache_key,
                                            served_from_cache=True, origin=origin, trace_id=req.trace_id, streamed=True, api_auth=api_auth)
                    metrics_registry.increment("chat_cache_hit_total")
                    _record_chat_outcome_metrics(answer_mode=str(hit.get("answer_mode") or "grounded"))
                    # Same interaction record as the /chat cache hit (the stream used to skip it).
                    records_hook.record_chat(api_auth, request, tenant_id=tenant_id, question=req.question,
                                             payload=hit, request_id=hit_request_id, trace_id=req.trace_id,
                                             source="cache")
                    yield _sse_event("final", hit)
                    return
            llm_context = _extractive_only_context(answer_policy, tenant_id)
            client = (
                ensure_openai_client(base_url=config.OPENAI_BASE_URL)
                if config.resolve_chat_generation_mode() == "llm" and answer_policy.get("allow_llm_calls", True)
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
            for name, payload in _iterate_in_context(llm_context, search_mode.iterate_in_mode(
                bool(req.deep), answer_query_stream(question_for_answer, **stream_kwargs)
            )):
                if name == "final":
                    result, trace = payload
                    request_id = _server_request_id(trace)
                    final_payload = result.to_dict()
                    final_payload.update(_collection_meta(staging_collection))
                    final_payload["request_id"] = request_id
                    if req.trace_id:
                        final_payload["trace_id"] = req.trace_id
                    if conversation_trace:
                        final_payload["conversation_context"] = conversation_trace
                    final_payload["answer_mode"] = trace.get("answer_mode")
                    final_payload["chat_generation_mode"] = trace.get(
                        "chat_generation_mode", config.resolve_chat_generation_mode()
                    )
                    final_payload["rag_profile_id"] = trace.get("rag_profile_id", rag_profile_id)
                    final_payload["question_type"] = trace.get("question_type", "other")
                    final_payload["interpretation"] = trace.get("interpretation")
                    final_payload["service_notice"] = records_hook.service_notice(trace)
                    final_payload["profile_validation"] = _profile_validation_or_skipped(trace)
                    _mark_for_review(final_payload, None, answer_policy)
                    evidence = _record_answer_evidence(request_id, tenant_id, final_payload, trace=trace, collection=staging_collection,
                                                       cache_key=cache_key, trace_id=req.trace_id, streamed=True, api_auth=api_auth)
                    if cache_key is not None and (not answer_evidence.enabled() or evidence is not None) and _cacheable(final_payload, result.guard_reason, result.used_fallback):
                        answer_cache.put(cache_key, _cache_entry(final_payload, evidence=evidence))
                    yield _sse_event("final", final_payload)
                else:
                    yield _sse_event(name, payload)
            if result is not None and trace is not None:
                stream_event = {
                    "request_id": request_id,
                    "trace_id": req.trace_id or request_id,
                    "tenant_id": tenant_id,
                    "question": req.question,
                    "normalized_query": trace.get("normalized_query"),
                    "intent": trace.get("intent") or result.intent,
                    "guard_reason": trace.get("final_guard_reason") or result.guard_reason,
                    "used_fallback": trace.get("final_used_fallback", result.used_fallback),
                    "citations_count": trace.get("citations_count", len(result.citations)),
                    "top_source_docs": _top_source_docs(trace.get("after_rerank")),
                    "cited_source_docs": answer_evidence.cited_source_docs(final_payload.get("citations")),
                    "latency_ms": trace.get("latency_ms"),
                    "answer_mode": trace.get("answer_mode"),
                    "streamed": True,
                }
                _annotate_collection_event(stream_event, staging_collection)
                _annotate_chat_profile(stream_event, runtime_profile)
                if final_payload.get("review_required"):
                    stream_event["review_required"] = True
                    stream_event["review_reason"] = final_payload.get("review_reason")
                if not answer_policy.get("allow_llm_calls", True):
                    stream_event["llm_allowed"] = False
                append_audit_event("chat", stream_event)
                _record_stage_latency_metrics(
                    trace,
                    citations_count=len(final_payload.get("citations") or []),
                    guard_reason=final_payload.get("guard_reason"),
                )
                records_hook.record_chat(api_auth, request, tenant_id=tenant_id, question=req.question, payload=final_payload,
                                         trace=trace, request_id=request_id, trace_id=req.trace_id, source="stream")
                _record_chat_outcome_metrics(
                    answer_mode=trace.get("answer_mode"),
                    guard_reason=trace.get("final_guard_reason") or result.guard_reason,
                    used_fallback=bool(trace.get("final_used_fallback", result.used_fallback)),
                )
        except Exception as exc:
            request_stats.error_requests += 1
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

    return StreamingResponse(_run_in_captured_context(_events()), media_type="text/event-stream")


@router.post("/chat/product-preview")
def chat_product_preview(req: ProductPreviewChatRequest, api_auth: ApiAuthContext = Depends(require_api_auth_rate_limited)):
    request_stats.total_requests += 1
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
    feedback_token = generate_feedback_token(tenant_id)

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
            approved_mode = _approved_answer_mode(approved)
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
                **_live_similar_kwargs(tenant_id),
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
        request_stats.error_requests += 1
        metrics_registry.note_error()
        logging.exception("chat_product_preview failed trace_id=%s", trace_id)
        raise HTTPException(status_code=500, detail="internal error")


@router.post("/chat/feedback")
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
    # With FEEDBACK_TOKEN_SECRET the token must be one this server issued for this tenant
    # (a made-up or another tenant's token is refused; an expired one is told apart).
    verdict = verify_feedback_token(feedback_token, normalize_tenant_id(req.tenant_id))
    if verdict == "expired":
        raise HTTPException(status_code=400, detail="feedback_token has expired")
    if verdict == "invalid":
        raise HTTPException(status_code=400, detail="invalid feedback_token")

    # Observability only: feedback_type is a stable enum label (good / bad /
    # neutral / human_review_requested); never a raw key, query, or document.
    metrics_registry.increment("chat_feedback_total", feedback_type)

    event = _feedback_audit_event(
        req,
        feedback_token=feedback_token,
        feedback_type=feedback_type,
    )
    # linked: request_id names a server evidence record of this tenant (an answer this server gave); a made-up or
    # another tenant's id stays unlinked.
    linked = bool(req.request_id and answer_evidence.lookup_evidence(req.request_id, normalize_tenant_id(req.tenant_id), access_scope=answer_cache.access_scope_digest(api_auth)))
    event["linked"] = linked
    stored = append_feedback_audit_event(event)
    response: Dict[str, Any] = {
        "ok": True,
        "feedback_token": event["feedback_token"],
        "feedback_type": feedback_type,
        "stored": stored,
        "linked": linked,
    }
    if not stored:
        response["warning"] = "feedback_logging_failed"
    return response


@router.get("/chat/suggest")
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


@router.post("/search")
def search(req: SearchRequest, api_auth: ApiAuthContext = Depends(require_api_auth_rate_limited)):
    request_stats.total_requests += 1
    metrics_registry.note_request()
    tenant_id = normalize_tenant_id(req.tenant_id)
    enforce_tenant_authorization(api_auth, tenant_id)
    set_audit_actor(api_auth)
    search_policy = resolve_chat_answer_policy(tenant_id)
    _enforce_chat_answer_policy(search_policy, tenant_id=tenant_id, endpoint="search", req_question=req.query,
                                trace_id=req.trace_id)
    staging_collection = _resolve_staging_collection(req.staging_collection, tenant_id=tenant_id)
    try:
        top_k = req.top_k or config.TOP_K

        # Approved QA exact lookup:
        # 確定QAと質問が完全一致する場合は、vector検索より前に必ず返す。
        # staging_collection指定時はステージング検証を優先するため exact bypass はしない。
        # テナントの answer_policy.allow_exact_answer=false なら使わない。現在も承認済みの項目だけを返す。
        if _approved_lookups_allowed(search_policy, staging_collection) and getattr(config, "APPROVED_QA_ENABLED", False):
            exact_hits = lookup_approved_qa_exact(req.query, tenant_id=tenant_id, limit=top_k,
                                                  live_validator=_live_approved_validator(tenant_id))
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
                {"text": h.text, "metadata": {k: v for k, v in h.metadata.items() if k not in {"_evidence_source_row_sha256", "_evidence_parent_generation"}}, "score": h.score}
                for h in hits
            ]
        }
    except Exception:
        request_stats.error_requests += 1
        metrics_registry.note_error()
        logging.exception("search failed trace_id=%s", req.trace_id)
        raise HTTPException(status_code=500, detail="internal error")


@router.post("/search/debug")
def search_debug(req: SearchDebugRequest, api_auth: ApiAuthContext = Depends(require_search_debug_access)):
    request_stats.total_requests += 1
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
                **_live_similar_kwargs(tenant_id),
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
        request_stats.error_requests += 1
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
