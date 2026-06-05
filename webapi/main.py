from __future__ import annotations

import logging
import os
import time
import uuid
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import config
from rag_core import approved_similar
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
from rag_core.qa import answer_query_with_trace, debug_retrieve_with_trace, retrieve_chunks
from rag_core.retrieval import RetrievedChunk
from rag_core.utils import ensure_openai_client


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
app = FastAPI()
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


class ChatRequest(BaseModel):
    question: str
    top_k: Optional[int] = None
    max_context_chars: Optional[int] = None
    trace_id: Optional[str] = None


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
    top_k: Optional[int] = 3
    keyword_profile: Optional[str] = None
    threshold_profile: Optional[str] = None


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
    provider = (
        config.getenv_first("EMBED_PROVIDER", default="openai") or "openai"
    ).lower()
    if provider == "local":
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
    path = str(config.APPROVED_QA_PATH)
    if _approved_qa_index is None or _approved_qa_index_path != path:
        _approved_qa_index = load_approved_qa(path, tenant_id=tenant_id)
        _approved_qa_index_path = path
    return lookup_approved_answer(_approved_qa_index, question, tenant_id=tenant_id)


def _approved_chat_payload(answer: ApprovedAnswer) -> Dict[str, Any]:
    citations = []
    for idx, citation in enumerate(answer.approved_citations, start=1):
        citations.append(
            {
                "number": idx,
                "source_doc": citation.source_doc,
                "source_pages": list(citation.source_pages),
                "chunk_id": citation.chunk_id,
            }
        )
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
) -> Dict[str, Any]:
    return {
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


def _product_preview_audit_payload(
    audit_event: Dict[str, Any],
    *,
    candidate_count: int,
    top_k: int,
    auto_answer_suppressed_for_similar_candidates: bool,
    exact_match_checked: bool,
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
    return payload


def _append_product_preview_audit(decision: Dict[str, Any], audit_payload: Dict[str, Any]) -> List[str]:
    if append_product_preview_chat_audit_event(audit_payload):
        decision["audit_persisted"] = True
        return []
    decision["audit_persisted"] = False
    return ["product_preview_audit_logging_failed"]


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


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return {
        "uptime_seconds": int(time.time() - _start_time),
        "total_requests": _total_requests,
        "error_requests": _error_requests,
    }


@app.post("/chat")
def chat(req: ChatRequest):
    global _total_requests, _error_requests
    _total_requests += 1
    try:
        approved = _approved_qa_lookup(req.question)
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
            return payload

        client = ensure_openai_client(base_url=config.OPENAI_BASE_URL)
        ans, trace = answer_query_with_trace(
            req.question,
            client=client,
            top_k=req.top_k or config.TOP_K,
            max_context_chars=req.max_context_chars or config.MAX_CONTEXT_CHARS,
        )
        append_audit_event(
            "chat",
            {
                "request_id": trace.get("request_id"),
                "trace_id": req.trace_id or trace.get("request_id"),
                "tenant_id": "default",
                "question": req.question,
                "normalized_query": trace.get("normalized_query"),
                "intent": trace.get("intent") or ans.intent,
                "guard_reason": trace.get("final_guard_reason") or ans.guard_reason,
                "used_fallback": trace.get("final_used_fallback", ans.used_fallback),
                "citations_count": trace.get("citations_count", len(ans.citations)),
                "top_source_docs": _top_source_docs(trace.get("after_rerank")),
                "latency_ms": trace.get("latency_ms"),
            },
        )
        return ans.to_dict()
    except Exception:
        _error_requests += 1
        append_audit_event(
            "chat",
            {
                "request_id": None,
                "trace_id": req.trace_id,
                "tenant_id": "default",
                "question": req.question,
                "error": "internal error",
            },
        )
        logging.exception("chat failed trace_id=%s", req.trace_id)
        raise HTTPException(status_code=500, detail="internal error")


@app.post("/chat/product-preview")
def chat_product_preview(req: ProductPreviewChatRequest):
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
    feedback_token = generate_feedback_token()

    try:
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
            )
            audit_warnings = _append_product_preview_audit(
                decision,
                _product_preview_audit_payload(
                    audit_event,
                    candidate_count=0,
                    top_k=top_k,
                    auto_answer_suppressed_for_similar_candidates=False,
                    exact_match_checked=True,
                ),
            )
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
                profile_info={
                    "keyword_profile": keyword_profile,
                    "threshold_profile": threshold_profile,
                },
                warnings=audit_warnings,
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
                top_k=top_k,
            )
            decision_gate = approved_similar.decide_approved_similar_candidate(raw_candidates)

        candidate_contracts: List[Dict[str, Any]] = []
        for index, candidate in enumerate(raw_candidates[:top_k]):
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
            top_k=top_k,
            keyword_profile=keyword_profile,
            threshold_profile=threshold_profile,
            exact_match_checked=True,
            auto_answer_suppressed_for_similar_candidates=bool(candidate_contracts),
            audit_event=audit_event,
            decision=decision_gate,
        )
        warnings = (
            ["approved_similar_candidates_are_preview_only"]
            if candidate_contracts
            else ["no_approved_similar_candidate_found"]
        )
        warnings.extend(
            _append_product_preview_audit(
                decision,
                _product_preview_audit_payload(
                    audit_event,
                    candidate_count=len(candidate_contracts),
                    top_k=top_k,
                    auto_answer_suppressed_for_similar_candidates=bool(candidate_contracts),
                    exact_match_checked=True,
                ),
            )
        )
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
            profile_info={
                "keyword_profile": keyword_profile,
                "threshold_profile": threshold_profile,
            },
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
def chat_feedback(req: ProductFeedbackRequest):
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
def search(req: SearchRequest):
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
def search_debug(req: SearchDebugRequest):
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
        if generation_error:
            return JSONResponse(status_code=generation_error[0], content=generation_error[1])
        raise HTTPException(status_code=500, detail="internal error")
