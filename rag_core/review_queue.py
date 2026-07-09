from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple


DEFAULT_CHAT_EVENTS = Path("runs/audit/chat_events.jsonl")
DEFAULT_FEEDBACK_EVENTS = Path("runs/audit/feedback_events.jsonl")
DEFAULT_OUTPUT = Path("runs/review/review_queue.jsonl")

_MAX_STRING_CHARS = 1000
_MAX_QUERY_CHARS = 500
_MAX_REASON_CHARS = 300
_MAX_CANDIDATE_IDS = 20


def _bounded_text(value: Any, max_chars: int = _MAX_STRING_CHARS) -> str | None:
    if value is None:
        return None
    text = str(value)
    if len(text) <= max_chars:
        return text
    suffix = "...[truncated]"
    if max_chars <= len(suffix):
        return text[:max_chars]
    return text[: max_chars - len(suffix)] + suffix


def _bounded_id(value: Any) -> str | None:
    text = _bounded_text(value)
    if text is None or not text.strip():
        return None
    return text.strip()


def _bounded_id_list(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    out: List[str] = []
    seen: set[str] = set()
    for value in values[:_MAX_CANDIDATE_IDS]:
        item = _bounded_id(value)
        if item is None or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def load_jsonl_safely(path: str | Path) -> Tuple[List[Dict[str, Any]], int]:
    input_path = Path(path)
    if not input_path.exists():
        return [], 0

    records: List[Dict[str, Any]] = []
    malformed = 0
    with input_path.open("r", encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if not raw:
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if not isinstance(record, dict):
                malformed += 1
                continue
            records.append(record)
    return records, malformed


def _chat_by_feedback_token(chat_events: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for event in chat_events:
        token = _bounded_id(event.get("feedback_token"))
        if token and token not in out:
            out[token] = event
    return out


def _feedback_by_token(feedback_events: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for event in feedback_events:
        token = _bounded_id(event.get("feedback_token"))
        if token:
            out.setdefault(token, []).append(event)
    return out


def _review_id(parts: Sequence[Any]) -> str:
    raw = "|".join(str(part or "") for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    return f"review_{digest}"


def _dedupe_key(chat: Dict[str, Any], feedback: Dict[str, Any] | None, reason: str) -> str:
    token = _bounded_id((feedback or {}).get("feedback_token") or chat.get("feedback_token"))
    request_id = _bounded_id(chat.get("request_id"))
    trace_id = _bounded_id(chat.get("trace_id"))
    return "|".join([token or "", request_id or "", trace_id or "", reason])


def _base_item(
    *,
    chat: Dict[str, Any],
    feedback: Dict[str, Any] | None,
    reason: str,
    priority: str,
    source: str,
    created_at: str,
) -> Dict[str, Any] | None:
    request_id = _bounded_text(chat.get("request_id"))
    trace_id = _bounded_text(chat.get("trace_id"))
    token = _bounded_text((feedback or {}).get("feedback_token") or chat.get("feedback_token"))
    if not token and not request_id and not trace_id:
        return None
    return {
        "review_id": _review_id([token, request_id, trace_id, reason]),
        "feedback_token": token,
        "request_id": request_id,
        "trace_id": trace_id,
        "tenant_id": _bounded_text(chat.get("tenant_id") or (feedback or {}).get("tenant_id") or "default") or "default",
        "user_query": _bounded_text(chat.get("user_query"), _MAX_QUERY_CHARS),
        "answer_mode": _bounded_text(chat.get("answer_mode")),
        "confidence_route": _bounded_text(chat.get("confidence_route")),
        "decision_route": _bounded_text(chat.get("decision_route")),
        "candidate_ids": _bounded_id_list(chat.get("candidate_ids")),
        "selected_candidate_id": _bounded_text((feedback or {}).get("selected_candidate_id")),
        "feedback_type": _bounded_text((feedback or {}).get("feedback_type")),
        "bad_reason": _bounded_text((feedback or {}).get("bad_reason"), _MAX_REASON_CHARS),
        "priority": priority,
        "status": "open",
        "created_at": created_at,
        "source": source,
        "reasons": [_bounded_text(reason, _MAX_REASON_CHARS)],
    }


def _feedback_review_items(
    *,
    chat: Dict[str, Any],
    feedback: Dict[str, Any],
    created_at: str,
) -> List[Dict[str, Any]]:
    feedback_type = str(feedback.get("feedback_type") or "").strip()
    selected = _bounded_id(feedback.get("selected_candidate_id"))
    items: List[Dict[str, Any]] = []
    if feedback_type == "bad":
        reason = "bad_feedback_with_selected_candidate" if selected else "bad_feedback_without_selected_candidate"
        item = _base_item(
            chat=chat,
            feedback=feedback,
            reason=reason,
            priority="high" if selected else "medium",
            source="feedback",
            created_at=created_at,
        )
        if item is not None:
            items.append(item)
    elif feedback_type == "human_review_requested":
        item = _base_item(
            chat=chat,
            feedback=feedback,
            reason="human_review_requested",
            priority="high",
            source="feedback",
            created_at=created_at,
        )
        if item is not None:
            items.append(item)
    return items


def _chat_review_items(
    *,
    chat: Dict[str, Any],
    has_bad_feedback: bool,
    has_human_review_feedback: bool,
    created_at: str,
) -> List[Dict[str, Any]]:
    answer_mode = str(chat.get("answer_mode") or "").strip()
    if answer_mode == "fallback_no_answer":
        item = _base_item(
            chat=chat,
            feedback=None,
            reason="fallback_no_answer",
            priority="medium",
            source="chat_event",
            created_at=created_at,
        )
        return [item] if item is not None else []
    if (
        answer_mode == "approved_similar_candidate_only"
        and not has_bad_feedback
        and not has_human_review_feedback
    ):
        item = _base_item(
            chat=chat,
            feedback=None,
            reason="approved_similar_candidate_only",
            priority="low",
            source="chat_event",
            created_at=created_at,
        )
        return [item] if item is not None else []
    return []


def build_review_queue(
    *,
    chat_events: str | Path = DEFAULT_CHAT_EVENTS,
    feedback_events: str | Path = DEFAULT_FEEDBACK_EVENTS,
    output: str | Path = DEFAULT_OUTPUT,
) -> Dict[str, Any]:
    chat_records, malformed_chat = load_jsonl_safely(chat_events)
    feedback_records, malformed_feedback = load_jsonl_safely(feedback_events)
    chats_by_token = _chat_by_feedback_token(chat_records)
    feedback_by_token = _feedback_by_token(feedback_records)
    created_at = datetime.now(timezone.utc).isoformat()

    items: List[Dict[str, Any]] = []
    seen: set[str] = set()
    joined_feedback_events = 0
    skipped_duplicates = 0
    skipped_missing_required_fields = 0

    for feedback in feedback_records:
        token = _bounded_id(feedback.get("feedback_token"))
        if token is None:
            skipped_missing_required_fields += 1
            continue
        chat = chats_by_token.get(token)
        if chat is None:
            skipped_missing_required_fields += 1
            continue
        joined_feedback_events += 1
        for item in _feedback_review_items(chat=chat, feedback=feedback, created_at=created_at):
            reason = str((item.get("reasons") or [""])[0])
            key = _dedupe_key(chat, feedback, reason)
            if key in seen:
                skipped_duplicates += 1
                continue
            seen.add(key)
            items.append(item)

    for chat in chat_records:
        token = _bounded_id(chat.get("feedback_token"))
        related = feedback_by_token.get(token or "", [])
        has_bad_feedback = any(str(item.get("feedback_type") or "") == "bad" for item in related)
        has_human_review = any(
            str(item.get("feedback_type") or "") == "human_review_requested"
            for item in related
        )
        for item in _chat_review_items(
            chat=chat,
            has_bad_feedback=has_bad_feedback,
            has_human_review_feedback=has_human_review,
            created_at=created_at,
        ):
            reason = str((item.get("reasons") or [""])[0])
            key = _dedupe_key(chat, None, reason)
            if key in seen:
                skipped_duplicates += 1
                continue
            seen.add(key)
            items.append(item)

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")

    return {
        "chat_events_loaded": len(chat_records),
        "feedback_events_loaded": len(feedback_records),
        "joined_feedback_events": joined_feedback_events,
        "review_items_written": len(items),
        "skipped_malformed_lines": malformed_chat + malformed_feedback,
        "skipped_duplicates": skipped_duplicates,
        "skipped_missing_required_fields": skipped_missing_required_fields,
        "output_path": str(output_path),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build product RAG review queue JSONL.")
    parser.add_argument("--chat-events", default=str(DEFAULT_CHAT_EVENTS))
    parser.add_argument("--feedback-events", default=str(DEFAULT_FEEDBACK_EVENTS))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)

    summary = build_review_queue(
        chat_events=args.chat_events,
        feedback_events=args.feedback_events,
        output=args.output,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
