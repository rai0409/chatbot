from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Sequence

from rag_core.source_metadata import bounded_safe_string, normalize_citation, normalize_source_metadata, normalize_source_pages


ANSWER_MODE_APPROVED_EXACT_MATCH = "approved_exact_match"
ANSWER_MODE_APPROVED_ALIAS_MATCH = "approved_alias_match"
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
    ANSWER_MODE_APPROVED_ALIAS_MATCH,
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
_PRESERVED_CITATION_KEYS = (
    "source_doc",
    "source_pages",
    "chunk_id",
    "title",
    "doc_version",
    "tenant_id",
)


def _bounded(value: Any, max_chars: int) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    suffix = "...[truncated]"
    if max_chars <= len(suffix):
        return text[:max_chars]
    return text[: max_chars - len(suffix)] + suffix


_TOKEN_VERSION = "v1"
_DEFAULT_TOKEN_TTL_SECONDS = 86400


def _token_secret() -> bytes | None:
    raw = str(os.getenv("FEEDBACK_TOKEN_SECRET", "") or "").strip()
    return raw.encode("utf-8") if raw else None


def feedback_signing_enabled() -> bool:
    return _token_secret() is not None


def _token_ttl_seconds() -> int:
    try:
        return max(1, int(str(os.getenv("FEEDBACK_TOKEN_TTL_SECONDS", "") or _DEFAULT_TOKEN_TTL_SECONDS)))
    except ValueError:
        return _DEFAULT_TOKEN_TTL_SECONDS


def _token_signature(secret: bytes, nonce: str, expires: int, tenant_id: str) -> str:
    message = f"{_TOKEN_VERSION}|{nonce}|{expires}|{tenant_id}".encode("utf-8")
    digest = hmac.new(secret, message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def generate_feedback_token(tenant_id: str | None = None, *, now: float | None = None) -> str:
    """With FEEDBACK_TOKEN_SECRET: "v1.<nonce>.<expiry>.<HMAC>", bound to the tenant and expiring
    (FEEDBACK_TOKEN_TTL_SECONDS, default one day). Without it: an opaque random token (development)."""
    secret = _token_secret()
    if secret is None:
        return secrets.token_urlsafe(18)
    nonce = secrets.token_urlsafe(12)
    expires = int((time.time() if now is None else now) + _token_ttl_seconds())
    tenant = str(tenant_id or "default").strip() or "default"
    return f"{_TOKEN_VERSION}.{nonce}.{expires}.{_token_signature(secret, nonce, expires, tenant)}"


def verify_feedback_token(token: str, tenant_id: str | None, *, now: float | None = None) -> str:
    """"ok", "expired", "invalid" (forged, other tenant or other secret) or "unsigned" (signing is off)."""
    secret = _token_secret()
    if secret is None:
        return "unsigned"
    parts = str(token or "").split(".")
    if len(parts) != 4 or parts[0] != _TOKEN_VERSION or not parts[2].isdigit():
        return "invalid"
    _version, nonce, expires_text, signature = parts
    tenant = str(tenant_id or "default").strip() or "default"
    expected = _token_signature(secret, nonce, int(expires_text), tenant)
    if not hmac.compare_digest(expected, signature):
        return "invalid"
    if (time.time() if now is None else now) > int(expires_text):
        return "expired"
    return "ok"


def build_candidate_contract(candidate: Dict[str, Any], *, max_preview_chars: int = _DEFAULT_PREVIEW_CHARS) -> Dict[str, Any]:
    source_metadata = normalize_source_metadata(
        {
            "source_id": candidate.get("source_id"),
            "source_title": candidate.get("source_title") or candidate.get("title"),
            "source_type": candidate.get("source_type"),
            "source_doc": candidate.get("source_doc"),
            "source_path": candidate.get("source_path"),
            "source_pages": candidate.get("source_pages") or [],
            "chunk_id": candidate.get("chunk_id") or candidate.get("id"),
            "parent_chunk_id": candidate.get("parent_chunk_id"),
            "version": candidate.get("version"),
            "checksum": candidate.get("checksum"),
            "checksum_algorithm": candidate.get("checksum_algorithm"),
            "status": candidate.get("status"),
            "updated_at": candidate.get("updated_at"),
            "tenant_id": candidate.get("tenant_id"),
            "category": candidate.get("category"),
            "doc_version": candidate.get("doc_version"),
            "doc_type": candidate.get("doc_type"),
            "chunk_type": candidate.get("chunk_type") or candidate.get("chunk_role"),
        }
    )
    source_metadata.setdefault("source_doc", candidate.get("source_doc"))
    source_metadata.setdefault("source_pages", [])
    source_metadata.setdefault("doc_version", candidate.get("doc_version"))
    source_metadata.setdefault("tenant_id", candidate.get("tenant_id"))
    source_metadata.setdefault("chunk_type", candidate.get("chunk_type"))
    source_metadata.setdefault("doc_type", candidate.get("doc_type"))
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
        "citations": _normalize_citations(candidate.get("citations") or []),
        "source_metadata": source_metadata,
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
        "citations": _normalize_citations(citations or []),
        "candidates": list(candidates or []),
        "decision": dict(decision or {}),
        "profile_info": dict(profile_info or {}),
        "warnings": list(warnings or []),
        "feedback_token": feedback_token or generate_feedback_token(tenant_id),
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
    normalized_input_question: str | None = None,
    matched_alias: str | None = None,
    canonical_question: str | None = None,
    retrieval_required: bool | None = None,
    llm_used: bool | None = None,
) -> Dict[str, Any]:
    event = {
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
    if normalized_input_question is not None:
        event["normalized_input_question"] = _bounded(normalized_input_question, max_query_chars)
    if matched_alias is not None:
        event["matched_alias"] = _bounded(matched_alias, max_query_chars)
    if canonical_question is not None:
        event["canonical_question"] = _bounded(canonical_question, max_query_chars)
    if retrieval_required is not None:
        event["retrieval_required"] = retrieval_required
    if llm_used is not None:
        event["llm_used"] = llm_used
    return event


def _normalize_citations(citations: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for citation in citations:
        if not isinstance(citation, dict):
            continue
        payload = normalize_citation(citation)
        for key in _PRESERVED_CITATION_KEYS:
            if key not in citation or key in payload:
                continue
            value = citation.get(key)
            if value is None:
                payload[key] = None
            elif key == "source_pages":
                payload[key] = normalize_source_pages(value)
            elif not isinstance(value, (dict, list, tuple, set)):
                text = bounded_safe_string(value)
                if text:
                    payload[key] = text
        normalized.append(payload)
    return normalized


def planned_safe_stages() -> List[str]:
    return [
        "approved_exact_match",
        "approved_similar_candidate",
        "normal_rag",
        "fallback",
        "human_escalation",
    ]
