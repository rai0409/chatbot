from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Sequence


ANSWER_MODE_APPROVED_EXACT_MATCH = "approved_exact_match"
ANSWER_MODE_APPROVED_SIMILAR_CANDIDATE_ONLY = "approved_similar_candidate_only"
ANSWER_MODE_RAG_ANSWER = "rag_answer"
ANSWER_MODE_FALLBACK_NO_ANSWER = "fallback_no_answer"
ANSWER_MODE_HUMAN_ESCALATION = "human_escalation"

CONFIDENCE_ROUTE_EXACT_MATCH = "exact_match"
CONFIDENCE_ROUTE_CANDIDATE_ONLY = "candidate_only"
CONFIDENCE_ROUTE_RAG = "rag"
CONFIDENCE_ROUTE_NO_ANSWER = "no_answer"
CONFIDENCE_ROUTE_HUMAN_REVIEW = "human_review"

SAFE_ANSWER_MODES = {
    ANSWER_MODE_APPROVED_EXACT_MATCH,
    ANSWER_MODE_APPROVED_SIMILAR_CANDIDATE_ONLY,
    ANSWER_MODE_RAG_ANSWER,
    ANSWER_MODE_FALLBACK_NO_ANSWER,
    ANSWER_MODE_HUMAN_ESCALATION,
}
CONFIDENCE_ROUTES = {
    CONFIDENCE_ROUTE_EXACT_MATCH,
    CONFIDENCE_ROUTE_CANDIDATE_ONLY,
    CONFIDENCE_ROUTE_RAG,
    CONFIDENCE_ROUTE_NO_ANSWER,
    CONFIDENCE_ROUTE_HUMAN_REVIEW,
}

_DEFAULT_PREVIEW_CHARS = 220
_DEFAULT_QUERY_CHARS = 500


def _bounded(value: Any, max_chars: int) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    suffix = "...[truncated]"
    if max_chars <= len(suffix):
        return text[:max_chars]
    return text[: max_chars - len(suffix)] + suffix


def generate_feedback_token() -> str:
    return secrets.token_urlsafe(18)


def build_candidate_contract(candidate: Dict[str, Any], *, max_preview_chars: int = _DEFAULT_PREVIEW_CHARS) -> Dict[str, Any]:
    item = {
        "qa_id": candidate.get("qa_id"),
        "question_text": candidate.get("question_text"),
        "approved_answer_preview": _bounded(
            candidate.get("approved_answer_preview")
            or candidate.get("answer_text_preview")
            or candidate.get("approved_answer")
            or candidate.get("answer_text"),
            max_preview_chars,
        ),
        "scores": {
            "hybrid_score": candidate.get("hybrid_score"),
            "semantic_score": candidate.get("semantic_score"),
            "keyword_score": candidate.get("keyword_score"),
            "weighted_keyword_score": candidate.get("weighted_keyword_score"),
            "top1_top2_margin": candidate.get("top1_top2_margin"),
        },
        "decision_route": candidate.get("decision_route") or candidate.get("route"),
        "matched_terms": list(candidate.get("matched_terms") or [])[:16],
        "citations": list(candidate.get("citations") or []),
        "source_metadata": {
            "source_doc": candidate.get("source_doc"),
            "source_pages": candidate.get("source_pages") or [],
            "doc_version": candidate.get("doc_version"),
            "tenant_id": candidate.get("tenant_id"),
            "chunk_type": candidate.get("chunk_type"),
            "doc_type": candidate.get("doc_type"),
        },
    }
    for key in (
        "feedback_preview_score_adjustment",
        "feedback_preview_reasons",
        "feedback_preview_positive_count",
        "feedback_preview_negative_count",
        "feedback_preview_review_needed_count",
        "feedback_preview_adjusted_score",
        "feature_rerank_applied",
        "feature_base_score",
        "feature_adjusted_score",
        "feature_score_adjustment",
        "feature_synonym_overlap_score",
        "feature_business_term_overlap_score",
        "feature_negative_mismatch",
        "feature_rerank_reasons",
        "feature_matched_canonicals",
        "feature_negative_mismatch_reason",
    ):
        if key in candidate:
            item[key] = candidate.get(key)
    return item


def build_product_answer_envelope(
    *,
    request_id: str | None,
    trace_id: str | None,
    tenant_id: str = "default",
    answer_mode: str,
    answer_text: str = "",
    confidence_route: str,
    citations: Sequence[Dict[str, Any]] | None = None,
    candidates: Sequence[Dict[str, Any]] | None = None,
    decision: Dict[str, Any] | None = None,
    profile_info: Dict[str, Any] | None = None,
    warnings: Sequence[str] | None = None,
    feedback_token: str | None = None,
) -> Dict[str, Any]:
    if answer_mode not in SAFE_ANSWER_MODES:
        raise ValueError(f"unsupported answer_mode: {answer_mode}")
    if confidence_route not in CONFIDENCE_ROUTES:
        raise ValueError(f"unsupported confidence_route: {confidence_route}")
    safe_answer_text = "" if answer_mode == ANSWER_MODE_APPROVED_SIMILAR_CANDIDATE_ONLY else str(answer_text or "")
    return {
        "request_id": request_id,
        "trace_id": trace_id,
        "tenant_id": tenant_id,
        "answer_mode": answer_mode,
        "answer_text": safe_answer_text,
        "confidence_route": confidence_route,
        "citations": list(citations or []),
        "candidates": list(candidates or []),
        "decision": dict(decision or {}),
        "profile_info": dict(profile_info or {}),
        "warnings": list(warnings or []),
        "feedback_token": feedback_token or generate_feedback_token(),
    }


def build_audit_event(
    *,
    request_id: str | None,
    trace_id: str | None,
    tenant_id: str = "default",
    user_query: str,
    answer_mode: str,
    selected_qa_id: str | None = None,
    candidate_ids: Sequence[str] | None = None,
    decision_route: str | None = None,
    keyword_profile: str | None = None,
    threshold_profile: str | None = None,
    latency_ms: int | float | None = None,
    timestamp: str | None = None,
    feedback_token: str | None = None,
    max_query_chars: int = _DEFAULT_QUERY_CHARS,
) -> Dict[str, Any]:
    return {
        "request_id": request_id,
        "trace_id": trace_id,
        "tenant_id": tenant_id,
        "user_query": _bounded(user_query, max_query_chars),
        "answer_mode": answer_mode,
        "selected_qa_id": selected_qa_id,
        "candidate_ids": list(candidate_ids or []),
        "decision_route": decision_route,
        "keyword_profile": keyword_profile,
        "threshold_profile": threshold_profile,
        "latency_ms": latency_ms,
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "feedback_token": feedback_token or generate_feedback_token(),
    }


def planned_safe_stages() -> List[str]:
    return [
        "approved_exact_match",
        "approved_similar_candidate",
        "normal_rag",
        "fallback",
        "human_escalation",
    ]
