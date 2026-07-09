from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple


DEFAULT_CHAT_EVENTS = Path("runs/audit/chat_events.jsonl")
DEFAULT_FEEDBACK_EVENTS = Path("runs/audit/feedback_events.jsonl")
DEFAULT_REVIEW_QUEUE = Path("runs/review/review_queue.jsonl")
DEFAULT_REVIEW_ACTIONS = Path("runs/review/review_actions.jsonl")
DEFAULT_OUTPUT = Path("runs/metrics/product_metrics.json")


def load_jsonl_safely(path: str | Path) -> Tuple[List[Dict[str, Any]], int, bool]:
    input_path = Path(path)
    if not input_path.exists():
        return [], 0, True

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
    return records, malformed, False


def _inc(counter: Dict[str, int], key: Any) -> None:
    label = str(key or "unknown")
    counter[label] = counter.get(label, 0) + 1


def _rate(numerator: int | float, denominator: int | float) -> float:
    if not denominator:
        return 0.0
    return round(float(numerator) / float(denominator), 6)


def _numeric_latency(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return number


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return round(sorted_values[0], 6)
    rank = (len(sorted_values) - 1) * percentile
    lower = int(rank)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = rank - lower
    value = sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * fraction
    return round(value, 6)


def _chat_metrics(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    answer_mode_breakdown: Dict[str, int] = {}
    decision_route_breakdown: Dict[str, int] = {}
    tenant_breakdown: Dict[str, int] = {}
    latencies: List[float] = []

    for record in records:
        _inc(answer_mode_breakdown, record.get("answer_mode"))
        _inc(decision_route_breakdown, record.get("decision_route") or record.get("confidence_route"))
        _inc(tenant_breakdown, record.get("tenant_id") or "default")
        latency = _numeric_latency(record.get("latency_ms"))
        if latency is not None:
            latencies.append(latency)

    total = len(records)
    return {
        "total_questions": total,
        "exact_match_count": answer_mode_breakdown.get("approved_exact_match", 0),
        "candidate_only_count": answer_mode_breakdown.get("approved_similar_candidate_only", 0),
        "fallback_no_answer_count": answer_mode_breakdown.get("fallback_no_answer", 0),
        "rag_answer_count": answer_mode_breakdown.get("rag_answer", 0),
        "human_escalation_count": answer_mode_breakdown.get("human_escalation", 0),
        "answer_mode_breakdown": answer_mode_breakdown,
        "decision_route_breakdown": decision_route_breakdown,
        "tenant_breakdown": tenant_breakdown,
        "avg_latency_ms": round(sum(latencies) / len(latencies), 6) if latencies else None,
        "p50_latency_ms": _percentile(latencies, 0.50),
        "p95_latency_ms": _percentile(latencies, 0.95),
    }


def _feedback_metrics(records: Sequence[Dict[str, Any]], *, total_questions: int) -> Dict[str, Any]:
    feedback_type_breakdown: Dict[str, int] = {}
    bad_reason_breakdown: Dict[str, int] = {}
    for record in records:
        feedback_type = str(record.get("feedback_type") or "unknown")
        _inc(feedback_type_breakdown, feedback_type)
        if feedback_type == "bad":
            _inc(bad_reason_breakdown, record.get("bad_reason"))

    total = len(records)
    good = feedback_type_breakdown.get("good", 0)
    bad = feedback_type_breakdown.get("bad", 0)
    neutral = feedback_type_breakdown.get("neutral", 0)
    human_review = feedback_type_breakdown.get("human_review_requested", 0)
    return {
        "total_feedback": total,
        "good_feedback_count": good,
        "bad_feedback_count": bad,
        "neutral_feedback_count": neutral,
        "human_review_requested_count": human_review,
        "feedback_type_breakdown": feedback_type_breakdown,
        "bad_reason_breakdown": bad_reason_breakdown,
        "good_feedback_rate": _rate(good, total),
        "bad_feedback_rate": _rate(bad, total),
        "feedback_coverage_rate": _rate(total, total_questions),
    }


def _review_queue_metrics(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    priority_breakdown: Dict[str, int] = {}
    status_breakdown: Dict[str, int] = {}
    reason_breakdown: Dict[str, int] = {}
    for record in records:
        _inc(priority_breakdown, record.get("priority"))
        _inc(status_breakdown, record.get("status"))
        reasons = record.get("reasons")
        if isinstance(reasons, list) and reasons:
            for reason in reasons:
                _inc(reason_breakdown, reason)
        else:
            _inc(reason_breakdown, "unknown")

    return {
        "total_review_items": len(records),
        "open_review_items": status_breakdown.get("open", 0),
        "high_priority_review_items": priority_breakdown.get("high", 0),
        "medium_priority_review_items": priority_breakdown.get("medium", 0),
        "low_priority_review_items": priority_breakdown.get("low", 0),
        "review_priority_breakdown": priority_breakdown,
        "review_reason_breakdown": reason_breakdown,
        "review_status_breakdown": status_breakdown,
    }


def _review_action_metrics(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    action_type_breakdown: Dict[str, int] = {}
    status_after_breakdown: Dict[str, int] = {}
    for record in records:
        _inc(action_type_breakdown, record.get("action_type"))
        _inc(status_after_breakdown, record.get("status_after"))

    return {
        "total_review_actions": len(records),
        "action_type_breakdown": action_type_breakdown,
        "status_after_breakdown": status_after_breakdown,
        "resolved_action_count": status_after_breakdown.get("resolved", 0),
        "ignored_action_count": status_after_breakdown.get("ignored", 0),
        "needs_followup_count": status_after_breakdown.get("needs_followup", 0),
    }


def aggregate_product_metrics(
    *,
    chat_events: str | Path = DEFAULT_CHAT_EVENTS,
    feedback_events: str | Path = DEFAULT_FEEDBACK_EVENTS,
    review_queue: str | Path = DEFAULT_REVIEW_QUEUE,
    review_actions: str | Path = DEFAULT_REVIEW_ACTIONS,
    output: str | Path = DEFAULT_OUTPUT,
) -> Dict[str, Any]:
    chat_records, malformed_chat, missing_chat = load_jsonl_safely(chat_events)
    feedback_records, malformed_feedback, missing_feedback = load_jsonl_safely(feedback_events)
    review_records, malformed_review, missing_review = load_jsonl_safely(review_queue)
    action_records, malformed_actions, missing_actions = load_jsonl_safely(review_actions)

    chat = _chat_metrics(chat_records)
    feedback = _feedback_metrics(feedback_records, total_questions=chat["total_questions"])
    review_queue_metrics = _review_queue_metrics(review_records)
    review_actions_metrics = _review_action_metrics(action_records)

    total_questions = chat["total_questions"]
    metrics = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_paths": {
            "chat_events": str(chat_events),
            "feedback_events": str(feedback_events),
            "review_queue": str(review_queue),
            "review_actions": str(review_actions),
        },
        "chat": chat,
        "feedback": feedback,
        "review_queue": review_queue_metrics,
        "review_actions": review_actions_metrics,
        "quality_indicators": {
            "candidate_only_rate": _rate(chat["candidate_only_count"], total_questions),
            "fallback_no_answer_rate": _rate(chat["fallback_no_answer_count"], total_questions),
            "good_feedback_rate": feedback["good_feedback_rate"],
            "bad_feedback_rate": feedback["bad_feedback_rate"],
            "human_review_requested_rate": _rate(
                feedback["human_review_requested_count"],
                feedback["total_feedback"],
            ),
            "review_item_rate": _rate(review_queue_metrics["total_review_items"], total_questions),
        },
        "data_quality": {
            "skipped_malformed_lines": malformed_chat + malformed_feedback + malformed_review + malformed_actions,
            "missing_input_files": [
                str(path)
                for path, missing in (
                    (chat_events, missing_chat),
                    (feedback_events, missing_feedback),
                    (review_queue, missing_review),
                    (review_actions, missing_actions),
                )
                if missing
            ],
        },
    }

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return metrics


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate product RAG operations metrics.")
    parser.add_argument("--chat-events", default=str(DEFAULT_CHAT_EVENTS))
    parser.add_argument("--feedback-events", default=str(DEFAULT_FEEDBACK_EVENTS))
    parser.add_argument("--review-queue", default=str(DEFAULT_REVIEW_QUEUE))
    parser.add_argument("--review-actions", default=str(DEFAULT_REVIEW_ACTIONS))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)

    metrics = aggregate_product_metrics(
        chat_events=args.chat_events,
        feedback_events=args.feedback_events,
        review_queue=args.review_queue,
        review_actions=args.review_actions,
        output=args.output,
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
