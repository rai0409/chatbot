from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple


DEFAULT_CHAT_EVENTS = Path("runs/audit/chat_events.jsonl")
DEFAULT_FEEDBACK_EVENTS = Path("runs/audit/feedback_events.jsonl")
DEFAULT_OUTPUT = Path("data/feedback/rerank_training_pairs.jsonl")
_MAX_QUERY_CHARS = 500
_MAX_STRING_CHARS = 1000
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
    text = _bounded_text(value, _MAX_STRING_CHARS)
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
    by_token: Dict[str, Dict[str, Any]] = {}
    for event in chat_events:
        token = _bounded_id(event.get("feedback_token"))
        if token and token not in by_token:
            by_token[token] = event
    return by_token


def _base_row(
    *,
    chat: Dict[str, Any],
    feedback: Dict[str, Any],
    feedback_token: str,
    signal: str,
    candidate_id: str | None,
    positive_candidate_id: str | None = None,
    negative_candidate_id: str | None = None,
) -> Dict[str, Any]:
    return {
        "feedback_token": feedback_token,
        "request_id": _bounded_text(chat.get("request_id")),
        "trace_id": _bounded_text(chat.get("trace_id")),
        "tenant_id": _bounded_text(chat.get("tenant_id") or feedback.get("tenant_id") or "default"),
        "user_query": _bounded_text(chat.get("user_query"), _MAX_QUERY_CHARS),
        "positive_candidate_id": positive_candidate_id,
        "negative_candidate_id": negative_candidate_id,
        "candidate_id": candidate_id,
        "signal": signal,
        "feedback_type": _bounded_text(feedback.get("feedback_type")),
        "bad_reason": _bounded_text(feedback.get("bad_reason")),
        "shown_rank": feedback.get("shown_rank"),
        "answer_mode": _bounded_text(chat.get("answer_mode")),
        "decision_route": _bounded_text(chat.get("decision_route")),
        "keyword_profile": _bounded_text(chat.get("keyword_profile")),
        "threshold_profile": _bounded_text(chat.get("threshold_profile")),
        "chat_timestamp": _bounded_text(chat.get("timestamp")),
        "feedback_timestamp": _bounded_text(feedback.get("timestamp")),
    }


def _shown_candidate_ids(chat: Dict[str, Any], feedback: Dict[str, Any]) -> List[str]:
    shown = _bounded_id_list(feedback.get("shown_candidate_ids"))
    if shown:
        return shown
    return _bounded_id_list(chat.get("candidate_ids"))


def _rows_for_feedback(
    *,
    chat: Dict[str, Any],
    feedback: Dict[str, Any],
    feedback_token: str,
) -> Tuple[List[Dict[str, Any]], bool]:
    user_query = _bounded_text(chat.get("user_query"), _MAX_QUERY_CHARS)
    if not user_query:
        return [], True

    feedback_type = str(feedback.get("feedback_type") or "").strip()
    selected = _bounded_id(feedback.get("selected_candidate_id"))
    shown = _shown_candidate_ids(chat, feedback)
    rows: List[Dict[str, Any]] = []

    if feedback_type == "good":
        if selected is None:
            return [], True
        negatives = [candidate_id for candidate_id in shown if candidate_id != selected]
        if not negatives:
            rows.append(
                _base_row(
                    chat=chat,
                    feedback=feedback,
                    feedback_token=feedback_token,
                    signal="good_positive",
                    candidate_id=selected,
                    positive_candidate_id=selected,
                )
            )
        else:
            for negative in negatives:
                rows.append(
                    _base_row(
                        chat=chat,
                        feedback=feedback,
                        feedback_token=feedback_token,
                        signal="good_positive_vs_shown_negative",
                        candidate_id=selected,
                        positive_candidate_id=selected,
                        negative_candidate_id=negative,
                    )
                )
        return rows, False

    if feedback_type == "bad":
        if selected is not None:
            rows.append(
                _base_row(
                    chat=chat,
                    feedback=feedback,
                    feedback_token=feedback_token,
                    signal="bad_selected_negative",
                    candidate_id=selected,
                    negative_candidate_id=selected,
                )
            )
            return rows, False
        if not shown:
            return [], True
        for candidate_id in shown:
            rows.append(
                _base_row(
                    chat=chat,
                    feedback=feedback,
                    feedback_token=feedback_token,
                    signal="bad_unselected_or_page",
                    candidate_id=candidate_id,
                    negative_candidate_id=candidate_id,
                )
            )
        return rows, False

    if feedback_type == "human_review_requested":
        candidates = [selected] if selected is not None else shown
        if not candidates:
            return [], True
        for candidate_id in candidates:
            rows.append(
                _base_row(
                    chat=chat,
                    feedback=feedback,
                    feedback_token=feedback_token,
                    signal="review_needed",
                    candidate_id=candidate_id,
                )
            )
        return rows, False

    if feedback_type == "neutral":
        candidates = [selected] if selected is not None else shown
        if not candidates:
            return [], True
        for candidate_id in candidates:
            rows.append(
                _base_row(
                    chat=chat,
                    feedback=feedback,
                    feedback_token=feedback_token,
                    signal="neutral",
                    candidate_id=candidate_id,
                )
            )
        return rows, False

    return [], True


def build_feedback_rerank_signals(
    *,
    chat_events: str | Path = DEFAULT_CHAT_EVENTS,
    feedback_events: str | Path = DEFAULT_FEEDBACK_EVENTS,
    output: str | Path = DEFAULT_OUTPUT,
) -> Dict[str, Any]:
    chat_records, malformed_chat = load_jsonl_safely(chat_events)
    feedback_records, malformed_feedback = load_jsonl_safely(feedback_events)
    chats_by_token = _chat_by_feedback_token(chat_records)

    output_rows: List[Dict[str, Any]] = []
    joined_feedback_events = 0
    skipped_missing_chat_event = 0
    skipped_missing_required_fields = 0

    for feedback in feedback_records:
        token = _bounded_id(feedback.get("feedback_token"))
        if token is None:
            skipped_missing_required_fields += 1
            continue
        chat = chats_by_token.get(token)
        if chat is None:
            skipped_missing_chat_event += 1
            continue
        joined_feedback_events += 1
        rows, skipped_required = _rows_for_feedback(
            chat=chat,
            feedback=feedback,
            feedback_token=token,
        )
        if skipped_required:
            skipped_missing_required_fields += 1
            continue
        output_rows.extend(rows)

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for row in output_rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    return {
        "chat_events_loaded": len(chat_records),
        "feedback_events_loaded": len(feedback_records),
        "joined_feedback_events": joined_feedback_events,
        "output_rows": len(output_rows),
        "skipped_malformed_lines": malformed_chat + malformed_feedback,
        "skipped_missing_chat_event": skipped_missing_chat_event,
        "skipped_missing_required_fields": skipped_missing_required_fields,
        "output_path": str(output_path),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build offline approved_similar rerank feedback signals."
    )
    parser.add_argument("--chat-events", default=str(DEFAULT_CHAT_EVENTS))
    parser.add_argument("--feedback-events", default=str(DEFAULT_FEEDBACK_EVENTS))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)

    summary = build_feedback_rerank_signals(
        chat_events=args.chat_events,
        feedback_events=args.feedback_events,
        output=args.output,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
