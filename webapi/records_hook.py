"""The one place where a finished chat exchange is handed to the records (S3, rag_core/records.py).

main.py calls `record_chat` after an answer is built. Whatever happens here, the answer goes out: a failure is logged and
nothing else. What is kept depends on the record level of the tenant (off keeps nothing)."""
from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

from rag_core import records


def service_notice(trace: Mapping[str, Any]) -> str:
    """The code the chat screen turns into a note on an answer: the AI could not write it (an endpoint failure, or a daily limit that was
    reached), so it is an excerpt of the documents. Empty when nothing went wrong."""
    error_type = (trace or {}).get("generation_error_type")
    if not error_type:
        return ""
    return "quota_exceeded" if error_type == "QuotaExceeded" else "llm_unavailable"


def _answer_text(payload: Mapping[str, Any]) -> str:
    return str(payload.get("answer_text") or payload.get("answer_with_footnotes") or "")


def record_chat(
    api_auth: Any, request: Any, *, tenant_id: str, question: str, payload: Mapping[str, Any],
    trace: Optional[Mapping[str, Any]] = None, request_id: Optional[str] = None, trace_id: Optional[str] = None, source: str = "chat",
) -> None:
    try:
        if records.effective_level(tenant_id) == "off":
            return
        trace = trace or {}
        client = getattr(request, "client", None)
        headers = getattr(request, "headers", None)
        records.record_interaction(
            tenant_id=tenant_id,
            request_id=request_id or trace.get("request_id") or payload.get("request_id"),
            trace_id=trace_id or payload.get("trace_id"),
            user=getattr(api_auth, "key_fingerprint", None),
            question=question,
            answer_text=_answer_text(payload),
            answer_mode=payload.get("answer_mode") or trace.get("answer_mode"),
            guard_reason=payload.get("guard_reason") or trace.get("final_guard_reason"),
            used_fallback=bool(payload.get("used_fallback")),
            citations=payload.get("citations"),
            latency_ms=trace.get("latency_ms"),
            generation_mode=payload.get("chat_generation_mode") or trace.get("chat_generation_mode"),
            degraded=bool(trace.get("generation_error_type")),
            intent=payload.get("intent") or trace.get("intent"),
            question_type=payload.get("question_type") or trace.get("question_type"),
            source=source,
            client_ip=getattr(client, "host", None) if client is not None else None,
            user_agent=headers.get("user-agent") if headers is not None else None,
        )
    except Exception:  # noqa: BLE001 - the records never break an answer
        logging.exception("recording a chat exchange failed")
