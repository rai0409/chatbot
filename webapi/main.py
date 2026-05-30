from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import config
from rag_core.audit_log import append_audit_event
from rag_core.qa import answer_query_with_trace, debug_retrieve_with_trace, retrieve_chunks
from rag_core.retrieval import RetrievedChunk
from rag_core.utils import ensure_openai_client


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
app = FastAPI()
_start_time = time.time()
_total_requests = 0
_error_requests = 0


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
        response = {
            "request_id": trace.get("request_id"),
            "trace_id": trace_id,
            "original_query": trace.get("original_query") or req.query,
            "normalized_query": trace.get("normalized_query"),
            "intent": trace.get("intent") or (ans.intent if ans is not None else None),
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
                "answer_mode": response["answer_mode"],
                "guard_reason": response["guard_reason"],
                "used_fallback": response["used_fallback"],
                "before_rerank_count": len(before_rerank),
                "after_rerank_count": len(after_rerank),
                "after_parent_expansion_count": len(after_parent_expansion),
                "citations_count": response["citations_count"],
                "latency_ms": response["latency_ms"],
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
