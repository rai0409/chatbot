from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

import config
from rag_core import answer_cache
from rag_core import approved_similar
from rag_core import approved_similar_feature_reranker
from rag_core import embedding_provider
from rag_core.approved_similar import search_approved_similar_candidates
from rag_core.approved_qa import ApprovedAnswer, ApprovedQAIndex, load_approved_qa, lookup_approved_answer
from rag_core.audit_log import (
    append_audit_event,
    append_feedback_audit_event,
    append_product_preview_chat_audit_event,
)
from rag_core.product_contract import (
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
from rag_core.qa import answer_query_stream, answer_query_with_trace, debug_retrieve_with_trace, retrieve_chunks
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
from webapi import metrics_registry
from webapi.admin_auth import require_admin_auth
from webapi.api_auth import require_api_auth, require_search_debug_access


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
app = FastAPI()


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
        allow_methods=["*"],
        allow_headers=["*"],
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


class ChatRequest(BaseModel):
    question: str
    top_k: Optional[int] = None
    max_context_chars: Optional[int] = None
    trace_id: Optional[str] = None
    tenant_id: Optional[str] = None


class SearchRequest(BaseModel):
    query: str
    top_k: Optional[int] = None
    trace_id: Optional[str] = None


class SearchDebugRequest(BaseModel):
    query: str
    top_k: Optional[int] = None
    max_context_chars: Optional[int] = None
    trace_id: Optional[str] = None
    include_context: bool = False
    generate_answer: bool = True
    include_approved_similar_candidates: bool = False
    approved_similar_top_k: Optional[int] = None


class ProductPreviewChatRequest(BaseModel):
    query: Optional[str] = None
    message: Optional[str] = None
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


@contextmanager
def _temporary_product_preview_profiles(
    *,
    keyword_profile: str | None,
    threshold_profile: str | None,
):
    previous_keyword = os.environ.get("APPROVED_SIMILAR_KEYWORD_WEIGHTS")
    previous_keyword_present = "APPROVED_SIMILAR_KEYWORD_WEIGHTS" in os.environ
    previous_thresholds = os.environ.get("APPROVED_SIMILAR_DECISION_THRESHOLDS")
    previous_thresholds_present = "APPROVED_SIMILAR_DECISION_THRESHOLDS" in os.environ
    try:
        if keyword_profile:
            os.environ["APPROVED_SIMILAR_KEYWORD_WEIGHTS"] = keyword_profile
            approved_similar._load_keyword_weight_profile.cache_clear()
        if threshold_profile:
            os.environ["APPROVED_SIMILAR_DECISION_THRESHOLDS"] = threshold_profile
            approved_similar._load_decision_threshold_config.cache_clear()
        yield
    finally:
        if previous_keyword_present:
            os.environ["APPROVED_SIMILAR_KEYWORD_WEIGHTS"] = str(previous_keyword)
        else:
            os.environ.pop("APPROVED_SIMILAR_KEYWORD_WEIGHTS", None)
        if previous_thresholds_present:
            os.environ["APPROVED_SIMILAR_DECISION_THRESHOLDS"] = str(previous_thresholds)
        else:
            os.environ.pop("APPROVED_SIMILAR_DECISION_THRESHOLDS", None)
        approved_similar._load_keyword_weight_profile.cache_clear()
        approved_similar._load_decision_threshold_config.cache_clear()


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
    global _approved_qa_index, _approved_qa_index_path
    if not getattr(config, "APPROVED_QA_ENABLED", False):
        return None
    tenant = normalize_tenant_id(tenant_id)
    path = str(config.APPROVED_QA_PATH)
    # Single-slot cache keyed by path AND tenant: load_approved_qa filters
    # records per tenant, so an index loaded for one tenant must never serve
    # another.
    cache_key = f"{path}::{tenant}"
    if _approved_qa_index is None or _approved_qa_index_path != cache_key:
        _approved_qa_index = load_approved_qa(path, tenant_id=tenant)
        _approved_qa_index_path = cache_key
    return lookup_approved_answer(_approved_qa_index, question, tenant_id=tenant)


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
    return {
        "answer_text": answer.approved_answer,
        "answer_with_footnotes": answer.approved_answer,
        "intent": "approved_exact_match",
        "guard_reason": None,
        "used_fallback": False,
        "citations": citations,
        "retrieved": [],
        "rewritten_query": "",
        "augmented_query": "",
        "answer_mode": "approved_exact_match",
        "approved_qa_id": answer.qa_id,
        "normalized_question": answer.normalized_question,
    }


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
                if status and item.get("status") != status:
                    continue
                if priority and item.get("priority") != priority:
                    continue
                if tenant_id and item.get("tenant_id") != tenant_id:
                    continue
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


@app.get("/health")
def health():
    payload: Dict[str, Any] = {"status": "ok"}
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
def metrics():
    # Counters are per-process; with multiple workers each process reports
    # its own numbers.
    return {
        "uptime_seconds": int(time.time() - _start_time),
        "total_requests": _total_requests,
        "error_requests": _error_requests,
        "counters": metrics_registry.snapshot(),
    }


@app.get("/product-preview", response_class=HTMLResponse)
def product_preview_page():
    path = _STATIC_DIR / "product_preview.html"
    try:
        return HTMLResponse(path.read_text(encoding="utf-8"))
    except Exception:
        logging.exception("product preview page unavailable")
        raise HTTPException(status_code=500, detail="product preview page unavailable")


@app.get("/admin/review", response_class=HTMLResponse)
def admin_review_page(_admin_auth: None = Depends(require_admin_auth)):
    path = _STATIC_DIR / "review_queue.html"
    try:
        return HTMLResponse(path.read_text(encoding="utf-8"))
    except Exception:
        logging.exception("review queue page unavailable")
        raise HTTPException(status_code=500, detail="review queue page unavailable")


@app.get("/admin/review/items")
def admin_review_items(
    status: Optional[str] = None,
    priority: Optional[str] = None,
    tenant_id: Optional[str] = None,
    limit: int = 100,
    _admin_auth: None = Depends(require_admin_auth),
):
    return _load_review_items(
        status=status,
        priority=priority,
        tenant_id=tenant_id,
        limit=limit,
    )


@app.post("/admin/review/action")
def admin_review_action(req: ReviewActionRequest, _admin_auth: None = Depends(require_admin_auth)):
    review_id = str(req.review_id or "").strip()
    action_type = str(req.action_type or "").strip()
    status_after = str(req.status_after or "").strip() if req.status_after is not None else None
    if not review_id:
        raise HTTPException(status_code=400, detail="review_id is required")
    if action_type not in ALLOWED_ACTION_TYPES:
        raise HTTPException(status_code=400, detail="invalid action_type")
    if status_after is not None and status_after not in ALLOWED_STATUS_AFTER:
        raise HTTPException(status_code=400, detail="invalid status_after")

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


@app.post("/chat")
def chat(req: ChatRequest, _api_auth: None = Depends(require_api_auth)):
    global _total_requests, _error_requests
    _total_requests += 1
    tenant_id = normalize_tenant_id(req.tenant_id)
    try:
        approved = _approved_qa_lookup(req.question, tenant_id=tenant_id)
        if approved is not None:
            payload = _approved_chat_payload(approved)
            append_audit_event(
                "chat",
                {
                    "request_id": req.trace_id,
                    "trace_id": req.trace_id,
                    "tenant_id": approved.tenant_id,
                    "question": req.question,
                    "normalized_question": approved.normalized_question,
                    "answer_mode": "approved_exact_match",
                    "approved_qa_id": approved.qa_id,
                    "citations_count": len(approved.approved_citations),
                },
            )
            _record_chat_outcome_metrics(answer_mode="approved_exact_match")
            return payload

        effective_top_k = req.top_k or config.TOP_K
        effective_max_context_chars = req.max_context_chars or config.MAX_CONTEXT_CHARS
        cache_key = None
        if answer_cache.enabled():
            cache_key = answer_cache.build_key(
                req.question,
                top_k=effective_top_k,
                max_context_chars=effective_max_context_chars,
                tenant_id=tenant_id,
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
                _record_chat_outcome_metrics(answer_mode="grounded")
                return cached

        client = ensure_openai_client(base_url=config.OPENAI_BASE_URL)
        ans, trace = answer_query_with_trace(
            req.question,
            client=client,
            top_k=effective_top_k,
            max_context_chars=effective_max_context_chars,
            tenant_id=tenant_id,
        )
        append_audit_event(
            "chat",
            {
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
            },
        )
        _record_chat_outcome_metrics(
            answer_mode=trace.get("answer_mode")
            or ("fallback" if (ans.guard_reason or ans.used_fallback) else "grounded"),
            guard_reason=trace.get("final_guard_reason") or ans.guard_reason,
            used_fallback=bool(trace.get("final_used_fallback", ans.used_fallback)),
        )
        payload = ans.to_dict()
        if cache_key is not None and ans.guard_reason is None and not ans.used_fallback:
            # Only clean grounded answers are cacheable; guard/no-answer and
            # extractive-fallback responses are never cached.
            answer_cache.put(cache_key, payload)
        return payload
    except Exception as exc:
        _error_requests += 1
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
def chat_stream(req: ChatRequest, _api_auth: None = Depends(require_api_auth)):
    global _total_requests
    _total_requests += 1
    tenant_id = normalize_tenant_id(req.tenant_id)

    def _events():
        global _error_requests
        try:
            approved = _approved_qa_lookup(req.question, tenant_id=tenant_id)
            if approved is not None:
                payload = _approved_chat_payload(approved)
                append_audit_event(
                    "chat",
                    {
                        "request_id": req.trace_id,
                        "trace_id": req.trace_id,
                        "tenant_id": approved.tenant_id,
                        "question": req.question,
                        "normalized_question": approved.normalized_question,
                        "answer_mode": "approved_exact_match",
                        "approved_qa_id": approved.qa_id,
                        "citations_count": len(approved.approved_citations),
                    },
                )
                _record_chat_outcome_metrics(answer_mode="approved_exact_match")
                yield _sse_event("approved", payload)
                return

            client = ensure_openai_client(base_url=config.OPENAI_BASE_URL)
            result = None
            trace = None
            for name, payload in answer_query_stream(
                req.question,
                client=client,
                top_k=req.top_k or config.TOP_K,
                max_context_chars=req.max_context_chars or config.MAX_CONTEXT_CHARS,
                tenant_id=tenant_id,
            ):
                if name == "final":
                    result, trace = payload
                    yield _sse_event("final", result.to_dict())
                else:
                    yield _sse_event(name, payload)
            if result is not None and trace is not None:
                append_audit_event(
                    "chat",
                    {
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
                    },
                )
                _record_chat_outcome_metrics(
                    answer_mode=trace.get("answer_mode"),
                    guard_reason=trace.get("final_guard_reason") or result.guard_reason,
                    used_fallback=bool(trace.get("final_used_fallback", result.used_fallback)),
                )
        except Exception as exc:
            _error_requests += 1
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
def chat_product_preview(req: ProductPreviewChatRequest, _api_auth: None = Depends(require_api_auth)):
    global _total_requests, _error_requests
    _total_requests += 1
    started = time.time()
    user_query = (req.query if req.query is not None else req.message) or ""
    user_query = user_query.strip()
    if not user_query:
        raise HTTPException(status_code=400, detail="query or message is required")

    request_id = str(uuid.uuid4())
    trace_id = request_id
    tenant_id = (req.tenant_id or "default").strip() or "default"
    top_k = max(1, min(int(req.top_k or 3), 10))
    keyword_profile = req.keyword_profile
    threshold_profile = req.threshold_profile
    rerank_profile = req.rerank_profile
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
            audit_event = build_audit_event(
                request_id=request_id,
                trace_id=trace_id,
                tenant_id=tenant_id,
                user_query=user_query,
                answer_mode=ANSWER_MODE_APPROVED_EXACT_MATCH,
                selected_qa_id=approved.qa_id,
                candidate_ids=[],
                decision_route=CONFIDENCE_ROUTE_EXACT_MATCH,
                keyword_profile=keyword_profile,
                threshold_profile=threshold_profile,
                latency_ms=latency_ms,
                feedback_token=feedback_token,
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
            return build_product_answer_envelope(
                request_id=request_id,
                trace_id=trace_id,
                tenant_id=tenant_id,
                answer_mode=ANSWER_MODE_APPROVED_EXACT_MATCH,
                answer_text=approved.approved_answer,
                confidence_route=CONFIDENCE_ROUTE_EXACT_MATCH,
                citations=_approved_product_citations(approved),
                candidates=[],
                decision=decision,
                profile_info=profile_info,
                warnings=response_warnings,
                feedback_token=feedback_token,
            )

        with _temporary_product_preview_profiles(
            keyword_profile=keyword_profile,
            threshold_profile=threshold_profile,
        ):
            client = _embedding_client()
            raw_candidates = approved_similar.search_approved_similar_candidates(
                user_query,
                client=client,
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
        logging.exception("chat_product_preview failed trace_id=%s", trace_id)
        raise HTTPException(status_code=500, detail="internal error")


@app.post("/chat/feedback")
def chat_feedback(req: ProductFeedbackRequest, _api_auth: None = Depends(require_api_auth)):
    feedback_token = str(req.feedback_token or "").strip()
    feedback_type = str(req.feedback_type or "").strip()
    if not feedback_token:
        raise HTTPException(status_code=400, detail="feedback_token is required")
    if feedback_type not in _ALLOWED_FEEDBACK_TYPES:
        raise HTTPException(status_code=400, detail="invalid feedback_type")

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


@app.post("/search")
def search(req: SearchRequest, _api_auth: None = Depends(require_api_auth)):
    global _total_requests, _error_requests
    _total_requests += 1
    try:
        client = _embedding_client()
        hits = retrieve_chunks(
            req.query, client=client, top_k=req.top_k or config.TOP_K
        )
        return {
            "hits": [
                {"text": h.text, "metadata": h.metadata, "score": h.score}
                for h in hits
            ]
        }
    except Exception:
        _error_requests += 1
        logging.exception("search failed trace_id=%s", req.trace_id)
        raise HTTPException(status_code=500, detail="internal error")


@app.post("/search/debug")
def search_debug(req: SearchDebugRequest, _access: None = Depends(require_search_debug_access)):
    global _total_requests, _error_requests
    _total_requests += 1
    try:
        client = _embedding_client()
        ans = None
        if req.generate_answer:
            ans, trace = answer_query_with_trace(
                req.query,
                client=client,
                top_k=req.top_k or config.TOP_K,
                max_context_chars=req.max_context_chars or config.MAX_CONTEXT_CHARS,
            )
        else:
            trace = debug_retrieve_with_trace(
                req.query,
                client=client,
                top_k=req.top_k or config.TOP_K,
                max_context_chars=req.max_context_chars or config.MAX_CONTEXT_CHARS,
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
        approved_exact = _approved_qa_lookup(req.query) if include_approved_similar else None
        approved_similar_candidates = []
        if include_approved_similar and approved_exact is None:
            approved_similar_candidates = search_approved_similar_candidates(
                req.query,
                client=client,
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
