from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple


DEFAULT_CASES = Path("data/feedback/rerank_eval_cases.jsonl")
DEFAULT_PROFILE = Path("configs/approved_similar_rerank_weights_feedback_preview.json")
DEFAULT_OUTPUT = Path("artifacts/eval/feedback_preview_comparison.json")

_QUERY_PREVIEW_CHARS = 180
_BASE_SCORE_KEYS = ("weighted_score", "final_score", "score", "similarity")


def _bounded_text(value: Any, max_chars: int = _QUERY_PREVIEW_CHARS) -> str | None:
    if value is None:
        return None
    text = str(value)
    if len(text) <= max_chars:
        return text
    suffix = "...[truncated]"
    if max_chars <= len(suffix):
        return text[:max_chars]
    return text[: max_chars - len(suffix)] + suffix


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_text(record: Dict[str, Any], keys: Sequence[str]) -> str | None:
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def load_jsonl_safely(path: str | Path) -> Tuple[List[Dict[str, Any]], int, bool]:
    input_path = Path(path)
    if not input_path.exists():
        return [], 0, True

    rows: List[Dict[str, Any]] = []
    malformed = 0
    with input_path.open("r", encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if not isinstance(row, dict):
                malformed += 1
                continue
            rows.append(row)
    return rows, malformed, False


def _candidate_score(candidate: Dict[str, Any]) -> float | None:
    scores = candidate.get("scores")
    if isinstance(scores, dict):
        for key in _BASE_SCORE_KEYS:
            score = _float_or_none(scores.get(key))
            if score is not None:
                return score
    for key in _BASE_SCORE_KEYS:
        score = _float_or_none(candidate.get(key))
        if score is not None:
            return score
    return None


def _normalize_candidate(candidate: Dict[str, Any], index: int) -> Dict[str, Any] | None:
    qa_id = _first_text(candidate, ("qa_id", "candidate_id", "id"))
    if not qa_id:
        return None
    return {
        "qa_id": qa_id,
        "base_score": _candidate_score(candidate),
        "original_rank": index + 1,
    }


def _normalize_case(row: Dict[str, Any], index: int) -> Dict[str, Any]:
    raw_candidates = row.get("candidates")
    candidates = [
        normalized
        for candidate_index, candidate in enumerate(raw_candidates if isinstance(raw_candidates, list) else [])
        if isinstance(candidate, dict)
        if (normalized := _normalize_candidate(candidate, candidate_index)) is not None
    ]
    return {
        "case_id": _first_text(row, ("case_id", "id")) or f"case_{index + 1}",
        "query": _bounded_text(_first_text(row, ("query", "user_query"))),
        "expected_qa_id": _first_text(row, ("expected_qa_id", "expected", "expected_id")),
        "candidates": candidates,
    }


def _rank_candidates(candidates: Sequence[Dict[str, Any]], *, adjustments: Dict[str, float] | None = None) -> List[Dict[str, Any]]:
    copied = [dict(candidate) for candidate in candidates]
    sortable = bool(copied) and all(candidate.get("base_score") is not None for candidate in copied)
    for candidate in copied:
        amount = float((adjustments or {}).get(candidate["qa_id"], 0.0))
        candidate["score_adjustment"] = amount
        if candidate.get("base_score") is not None:
            candidate["adjusted_score"] = round(float(candidate["base_score"]) + amount, 6)
    if sortable:
        copied.sort(
            key=lambda candidate: (
                float(candidate.get("adjusted_score") if adjustments is not None else candidate["base_score"]),
                -int(candidate["original_rank"]),
            ),
            reverse=True,
        )
    return copied


def _rank_of(ranked: Sequence[Dict[str, Any]], expected_qa_id: str | None) -> int | None:
    if not expected_qa_id:
        return None
    for index, candidate in enumerate(ranked, start=1):
        if candidate.get("qa_id") == expected_qa_id:
            return index
    return None


def _top_hit(rank: int | None, k: int) -> int:
    return 1 if rank is not None and rank <= k else 0


def _load_profile(path: str | Path) -> Tuple[Dict[str, Any], Dict[str, Any], bool]:
    profile_path = Path(path)
    info: Dict[str, Any] = {
        "loaded": False,
        "valid": False,
        "profile_name": None,
        "profile_type": None,
        "path": str(profile_path),
        "reason": None,
    }
    if not profile_path.exists():
        info["reason"] = "missing_profile"
        return {}, info, True
    try:
        payload = json.loads(profile_path.read_text(encoding="utf-8"))
    except Exception:
        info["loaded"] = True
        info["reason"] = "invalid_json"
        return {}, info, False
    if not isinstance(payload, dict):
        info["loaded"] = True
        info["reason"] = "profile_not_object"
        return {}, info, False

    info["loaded"] = True
    info["profile_name"] = payload.get("profile_name")
    info["profile_type"] = payload.get("profile_type")
    valid = True
    reason = None
    safety = payload.get("safety")
    if payload.get("production_enabled") is not False:
        valid = False
        reason = "production_enabled_must_be_false"
    elif payload.get("profile_name") is not None and payload.get("profile_name") != "feedback_preview":
        valid = False
        reason = "invalid_profile_name"
    elif payload.get("profile_type") is not None and payload.get("profile_type") != "approved_similar_feedback_rerank":
        valid = False
        reason = "invalid_profile_type"
    elif not isinstance(payload.get("candidate_adjustments"), dict):
        valid = False
        reason = "candidate_adjustments_must_be_object"
    elif isinstance(safety, dict) and safety.get("no_runtime_ranking_change") is not None and safety.get("no_runtime_ranking_change") is not True:
        valid = False
        reason = "unsafe_runtime_flag"

    info["valid"] = valid
    info["reason"] = reason
    if not valid:
        return {}, info, False
    adjustments = {
        str(qa_id): (_float_or_none(adjustment.get("score_adjustment")) or 0.0)
        for qa_id, adjustment in payload.get("candidate_adjustments", {}).items()
        if isinstance(adjustment, dict)
    }
    return adjustments, info, False


def _case_result(case: Dict[str, Any], adjustments: Dict[str, float]) -> Dict[str, Any]:
    candidates = case["candidates"]
    baseline = _rank_candidates(candidates)
    preview = _rank_candidates(candidates, adjustments=adjustments)
    baseline_rank = _rank_of(baseline, case["expected_qa_id"])
    preview_rank = _rank_of(preview, case["expected_qa_id"])
    adjusted_ids = [
        candidate["qa_id"]
        for candidate in candidates
        if candidate["qa_id"] in adjustments
    ]

    if baseline_rank is None or preview_rank is None:
        result = "missing_expected"
        rank_delta = None
    else:
        rank_delta = baseline_rank - preview_rank
        if rank_delta > 0:
            result = "improved"
        elif rank_delta < 0:
            result = "regressed"
        else:
            result = "unchanged"

    summary = {
        "case_id": case["case_id"],
        "query": case.get("query"),
        "expected_qa_id": case["expected_qa_id"],
        "baseline_rank": baseline_rank,
        "preview_rank": preview_rank,
        "rank_delta": rank_delta,
        "baseline_top_candidate_id": baseline[0]["qa_id"] if baseline else None,
        "preview_top_candidate_id": preview[0]["qa_id"] if preview else None,
        "adjusted_candidate_ids": adjusted_ids,
        "result": result,
    }
    return summary


def _empty_metrics() -> Dict[str, Any]:
    return {
        "total_cases": 0,
        "evaluated_cases": 0,
        "baseline_top1": 0,
        "baseline_top3": 0,
        "baseline_top5": 0,
        "preview_top1": 0,
        "preview_top3": 0,
        "preview_top5": 0,
        "top1_delta": 0,
        "top3_delta": 0,
        "top5_delta": 0,
        "improvement_count": 0,
        "regression_count": 0,
        "unchanged_count": 0,
        "adjusted_case_count": 0,
        "adjusted_candidate_count": 0,
        "missing_expected_count": 0,
        "skipped_malformed_lines": 0,
        "missing_input_files": [],
    }


def _build_metrics(results: Sequence[Dict[str, Any]], *, total_cases: int, malformed: int, missing_files: Sequence[str]) -> Dict[str, Any]:
    metrics = _empty_metrics()
    metrics["total_cases"] = total_cases
    metrics["skipped_malformed_lines"] = malformed
    metrics["missing_input_files"] = list(missing_files)
    adjusted_candidates = set()
    for result in results:
        for qa_id in result["adjusted_candidate_ids"]:
            adjusted_candidates.add(qa_id)
        if result["adjusted_candidate_ids"]:
            metrics["adjusted_case_count"] += 1
        if result["result"] == "missing_expected":
            metrics["missing_expected_count"] += 1
            continue
        metrics["evaluated_cases"] += 1
        metrics["baseline_top1"] += _top_hit(result["baseline_rank"], 1)
        metrics["baseline_top3"] += _top_hit(result["baseline_rank"], 3)
        metrics["baseline_top5"] += _top_hit(result["baseline_rank"], 5)
        metrics["preview_top1"] += _top_hit(result["preview_rank"], 1)
        metrics["preview_top3"] += _top_hit(result["preview_rank"], 3)
        metrics["preview_top5"] += _top_hit(result["preview_rank"], 5)
        if result["result"] == "improved":
            metrics["improvement_count"] += 1
        elif result["result"] == "regressed":
            metrics["regression_count"] += 1
        else:
            metrics["unchanged_count"] += 1
    metrics["top1_delta"] = metrics["preview_top1"] - metrics["baseline_top1"]
    metrics["top3_delta"] = metrics["preview_top3"] - metrics["baseline_top3"]
    metrics["top5_delta"] = metrics["preview_top5"] - metrics["baseline_top5"]
    metrics["adjusted_candidate_count"] = len(adjusted_candidates)
    return metrics


def evaluate_feedback_preview_rerank(
    *,
    cases_path: str | Path = DEFAULT_CASES,
    profile_path: str | Path = DEFAULT_PROFILE,
    output: str | Path = DEFAULT_OUTPUT,
) -> Dict[str, Any]:
    rows, malformed, missing_cases = load_jsonl_safely(cases_path)
    adjustments, profile_info, missing_profile = _load_profile(profile_path)
    missing_files: List[str] = []
    if missing_cases:
        missing_files.append(str(cases_path))
    if missing_profile:
        missing_files.append(str(profile_path))

    cases = [_normalize_case(row, index) for index, row in enumerate(rows)]
    results = [_case_result(case, adjustments) for case in cases]
    metrics = _build_metrics(
        results,
        total_cases=len(cases),
        malformed=malformed,
        missing_files=missing_files,
    )
    improvements = [result for result in results if result["result"] == "improved"]
    regressions = [result for result in results if result["result"] == "regressed"]
    unchanged = [result for result in results if result["result"] == "unchanged"]
    missing_expected = [result for result in results if result["result"] == "missing_expected"]

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_paths": {
            "cases": str(cases_path),
            "feedback_preview_profile": str(profile_path),
        },
        "profile_info": profile_info,
        "metrics": metrics,
        "improvements": improvements,
        "regressions": regressions,
        "unchanged": unchanged,
        "missing_expected": missing_expected,
        "data_quality": {
            "skipped_malformed_lines": malformed,
            "missing_expected_count": metrics["missing_expected_count"],
            "missing_input_files": missing_files,
        },
    }

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare baseline approved_similar ordering with feedback_preview adjusted ordering."
    )
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--profile", default=str(DEFAULT_PROFILE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)

    report = evaluate_feedback_preview_rerank(
        cases_path=args.cases,
        profile_path=args.profile,
        output=args.output,
    )
    print(json.dumps({"output_path": str(args.output), "metrics": report["metrics"], "profile_info": report["profile_info"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
