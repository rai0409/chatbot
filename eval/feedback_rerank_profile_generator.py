from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple


DEFAULT_INPUT = Path("data/feedback/rerank_training_pairs.jsonl")
DEFAULT_OUTPUT = Path("configs/approved_similar_rerank_weights_feedback_preview.json")

_MAX_STRING_CHARS = 1000
_MAX_REASONS = 8
_WEIGHTS = {
    "feedback_positive_boost": 0.03,
    "feedback_negative_penalty": 0.06,
    "review_needed_penalty": 0.03,
}
_LIMITS = {
    "max_positive_boost": 0.05,
    "max_negative_penalty": 0.10,
    "min_events_for_adjustment": 1,
}


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


def _candidate_id(value: Any) -> str | None:
    text = _bounded_text(value)
    if text is None or not text.strip():
        return None
    return text.strip()


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


def _signal_kind(row: Dict[str, Any]) -> str:
    signal = str(row.get("signal") or "").strip()
    feedback_type = str(row.get("feedback_type") or "").strip()
    if signal.startswith("good") or row.get("positive_candidate_id"):
        return "positive"
    if signal.startswith("bad") or row.get("negative_candidate_id") or feedback_type == "bad":
        return "negative"
    if signal == "review_needed" or feedback_type == "human_review_requested":
        return "review_needed"
    if signal == "neutral" or feedback_type == "neutral":
        return "neutral"
    return "unknown"


def _row_candidate_id(row: Dict[str, Any], kind: str) -> str | None:
    if kind == "positive":
        return _candidate_id(row.get("positive_candidate_id") or row.get("candidate_id"))
    if kind == "negative":
        return _candidate_id(row.get("negative_candidate_id") or row.get("candidate_id"))
    return _candidate_id(row.get("candidate_id"))


def _new_stats() -> Dict[str, Any]:
    return {
        "positive_count": 0,
        "negative_count": 0,
        "review_needed_count": 0,
        "neutral_count": 0,
        "tenant_ids": [],
        "answer_modes": [],
        "decision_routes": [],
        "keyword_profiles": [],
        "threshold_profiles": [],
    }


def _append_unique(stats: Dict[str, Any], key: str, value: Any, limit: int = 8) -> None:
    text = _bounded_text(value)
    if text is None or not text.strip():
        return
    items = stats[key]
    if text not in items and len(items) < limit:
        items.append(text)


def _aggregate(rows: Sequence[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, int], int]:
    by_candidate: Dict[str, Dict[str, Any]] = {}
    counts = {
        "positive_signals": 0,
        "negative_signals": 0,
        "review_needed_signals": 0,
        "neutral_signals": 0,
    }
    skipped_missing_candidate_id = 0

    for row in rows:
        kind = _signal_kind(row)
        candidate_id = _row_candidate_id(row, kind)
        if kind == "unknown" or candidate_id is None:
            skipped_missing_candidate_id += 1
            continue

        stats = by_candidate.setdefault(candidate_id, _new_stats())
        _append_unique(stats, "tenant_ids", row.get("tenant_id"))
        _append_unique(stats, "answer_modes", row.get("answer_mode"))
        _append_unique(stats, "decision_routes", row.get("decision_route"))
        _append_unique(stats, "keyword_profiles", row.get("keyword_profile"))
        _append_unique(stats, "threshold_profiles", row.get("threshold_profile"))

        if kind == "positive":
            stats["positive_count"] += 1
            counts["positive_signals"] += 1
        elif kind == "negative":
            stats["negative_count"] += 1
            counts["negative_signals"] += 1
        elif kind == "review_needed":
            stats["review_needed_count"] += 1
            counts["review_needed_signals"] += 1
        elif kind == "neutral":
            stats["neutral_count"] += 1
            counts["neutral_signals"] += 1

    return by_candidate, counts, skipped_missing_candidate_id


def _clamp_adjustment(value: float) -> float:
    return round(
        max(
            -float(_LIMITS["max_negative_penalty"]),
            min(float(_LIMITS["max_positive_boost"]), value),
        ),
        6,
    )


def _reasons(stats: Dict[str, Any]) -> List[str]:
    reasons: List[str] = []
    if stats["positive_count"]:
        reasons.append(f"positive feedback signals: {stats['positive_count']}")
    if stats["negative_count"]:
        reasons.append(f"negative feedback signals: {stats['negative_count']}")
    if stats["review_needed_count"]:
        reasons.append(f"review-needed signals: {stats['review_needed_count']}")
    if stats["neutral_count"]:
        reasons.append(f"neutral signals: {stats['neutral_count']}")
    return reasons[:_MAX_REASONS]


def _candidate_adjustment(stats: Dict[str, Any]) -> Dict[str, Any]:
    raw_adjustment = (
        stats["positive_count"] * float(_WEIGHTS["feedback_positive_boost"])
        - stats["negative_count"] * float(_WEIGHTS["feedback_negative_penalty"])
        - stats["review_needed_count"] * float(_WEIGHTS["review_needed_penalty"])
    )
    return {
        "positive_count": int(stats["positive_count"]),
        "negative_count": int(stats["negative_count"]),
        "review_needed_count": int(stats["review_needed_count"]),
        "neutral_count": int(stats["neutral_count"]),
        "score_adjustment": _clamp_adjustment(raw_adjustment),
        "reasons": _reasons(stats),
        "tenant_ids": list(stats["tenant_ids"]),
        "answer_modes": list(stats["answer_modes"]),
        "decision_routes": list(stats["decision_routes"]),
        "keyword_profiles": list(stats["keyword_profiles"]),
        "threshold_profiles": list(stats["threshold_profiles"]),
    }


def generate_feedback_rerank_profile(
    *,
    input_path: str | Path = DEFAULT_INPUT,
    output: str | Path = DEFAULT_OUTPUT,
) -> Dict[str, Any]:
    rows, malformed = load_jsonl_safely(input_path)
    aggregate, counts, skipped_missing_candidate_id = _aggregate(rows)
    candidate_adjustments = {
        candidate_id: _candidate_adjustment(stats)
        for candidate_id, stats in sorted(aggregate.items())
        if (
            stats["positive_count"]
            + stats["negative_count"]
            + stats["review_needed_count"]
            + stats["neutral_count"]
        )
        >= int(_LIMITS["min_events_for_adjustment"])
    }

    profile = {
        "profile_name": "feedback_preview",
        "profile_type": "approved_similar_feedback_rerank",
        "version": 1,
        "runtime_enabled": False,
        "production_enabled": False,
        "generated_from": {
            "input_path": str(input_path),
            "positive_signals": counts["positive_signals"],
            "negative_signals": counts["negative_signals"],
            "review_needed_signals": counts["review_needed_signals"],
            "neutral_signals": counts["neutral_signals"],
        },
        "global_weights": dict(_WEIGHTS),
        "candidate_adjustments": candidate_adjustments,
        "limits": dict(_LIMITS),
        "safety": {
            "no_runtime_ranking_change": True,
            "no_auto_answer_enablement": True,
            "requires_offline_evaluation_before_production": True,
        },
    }

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    return {
        "input_rows_loaded": len(rows),
        "output_candidate_adjustments": len(candidate_adjustments),
        "positive_signals": counts["positive_signals"],
        "negative_signals": counts["negative_signals"],
        "review_needed_signals": counts["review_needed_signals"],
        "neutral_signals": counts["neutral_signals"],
        "skipped_malformed_lines": malformed,
        "skipped_missing_candidate_id": skipped_missing_candidate_id,
        "output_path": str(output_path),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a disabled approved_similar feedback rerank preview profile."
    )
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)

    summary = generate_feedback_rerank_profile(
        input_path=args.input,
        output=args.output,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
