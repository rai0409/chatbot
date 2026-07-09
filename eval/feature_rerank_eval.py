from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from rag_core.approved_similar_feature_reranker import apply_feature_rerank, load_feature_rerank_profile
from rag_core.japanese_normalizer import load_japanese_business_synonyms


DEFAULT_CASES = Path("data/feedback/rerank_eval_cases.jsonl")
DEFAULT_FEEDBACK_PROFILE = Path("configs/approved_similar_rerank_weights_feedback_preview.json")
DEFAULT_FEATURE_PROFILE = Path("configs/approved_similar_feature_rerank_profile.json")
DEFAULT_SYNONYMS = Path("configs/japanese_business_synonyms.json")
DEFAULT_OUTPUT = Path("artifacts/eval/feature_rerank_comparison.json")

_QUERY_PREVIEW_CHARS = 180
_ANSWER_PREVIEW_CHARS = 220
_BASE_SCORE_KEYS = ("weighted_score", "final_score", "score", "similarity")
_MODES = ("feedback_preview", "feature_rerank", "combined_feedback_then_feature")
_RANK_KEYS = {
    "baseline": "baseline_rank",
    "feedback_preview": "feedback_preview_rank",
    "feature_rerank": "feature_rerank_rank",
    "combined_feedback_then_feature": "combined_rank",
}


def _bounded_text(value: Any, max_chars: int) -> str | None:
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
    approved_answer_preview = _first_text(candidate, ("approved_answer_preview", "answer_text_preview"))
    score = _candidate_score(candidate)
    normalized: Dict[str, Any] = {
        "qa_id": qa_id,
        "question_text": _first_text(candidate, ("question_text", "question")),
        "approved_answer_preview": _bounded_text(approved_answer_preview, _ANSWER_PREVIEW_CHARS),
        "base_score": score,
        "original_rank": index + 1,
    }
    if score is not None:
        normalized["score"] = score
        normalized["scores"] = {"weighted_score": score}
    return normalized


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
        "query": _bounded_text(_first_text(row, ("query", "user_query")), _QUERY_PREVIEW_CHARS) or "",
        "expected_qa_id": _first_text(row, ("expected_qa_id", "expected", "expected_id")),
        "candidates": candidates,
    }


def _rank_baseline(candidates: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ranked = [dict(candidate) for candidate in candidates]
    sortable = bool(ranked) and all(candidate.get("base_score") is not None for candidate in ranked)
    if sortable:
        ranked.sort(key=lambda c: (float(c["base_score"]), -int(c["original_rank"])), reverse=True)
    return ranked


def _load_feedback_adjustments(path: str | Path) -> Tuple[Dict[str, float], Dict[str, Any], bool]:
    profile_path = Path(path)
    info = {
        "loaded": False,
        "valid": False,
        "profile_name": None,
        "profile_type": None,
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
    info.update(
        {
            "loaded": True,
            "profile_name": payload.get("profile_name"),
            "profile_type": payload.get("profile_type"),
        }
    )
    safety = payload.get("safety")
    valid = (
        payload.get("production_enabled") is False
        and payload.get("profile_name") == "feedback_preview"
        and payload.get("profile_type") == "approved_similar_feedback_rerank"
        and isinstance(payload.get("candidate_adjustments"), dict)
        and isinstance(safety, dict)
        and safety.get("no_runtime_ranking_change") is True
        and safety.get("no_auto_answer_enablement") is True
        and safety.get("requires_offline_evaluation_before_production") is True
    )
    info["valid"] = valid
    if not valid:
        info["reason"] = "invalid_feedback_profile"
        return {}, info, False
    adjustments = {
        str(qa_id): (_float_or_none(adjustment.get("score_adjustment")) or 0.0)
        for qa_id, adjustment in payload.get("candidate_adjustments", {}).items()
        if isinstance(adjustment, dict)
    }
    return adjustments, info, False


def _load_feature_profile(path: str | Path) -> Tuple[Dict[str, Any] | None, Dict[str, Any], bool]:
    profile_path = Path(path)
    info = {
        "loaded": False,
        "valid": False,
        "profile_name": None,
        "profile_type": None,
        "reason": None,
    }
    if not profile_path.exists():
        info["reason"] = "missing_profile"
        return None, info, True
    try:
        profile = load_feature_rerank_profile(profile_path)
    except Exception:
        info["loaded"] = True
        info["reason"] = "invalid_profile"
        return None, info, False
    info.update(
        {
            "loaded": True,
            "profile_name": profile.get("profile_name"),
            "profile_type": profile.get("profile_type"),
        }
    )
    ranked, summary = apply_feature_rerank("__profile_check__", [], profile=profile, synonym_config={"synonym_groups": [], "negative_mismatch_pairs": []})
    del ranked
    valid = bool(summary.get("valid_profile"))
    info["valid"] = valid
    info["reason"] = summary.get("profile_invalid_reason")
    return (profile if valid else None), info, False


def _load_synonyms(path: str | Path) -> Tuple[Dict[str, Any], bool]:
    synonym_path = Path(path)
    if not synonym_path.exists():
        return {"synonym_groups": [], "negative_mismatch_pairs": []}, True
    try:
        return load_japanese_business_synonyms(synonym_path), False
    except ValueError:
        return {"synonym_groups": [], "negative_mismatch_pairs": []}, False


def _apply_feedback(candidates: Sequence[Dict[str, Any]], adjustments: Dict[str, float]) -> List[Dict[str, Any]]:
    ranked = [dict(candidate) for candidate in candidates]
    sortable = bool(ranked) and all(candidate.get("base_score") is not None for candidate in ranked)
    for candidate in ranked:
        amount = float(adjustments.get(candidate["qa_id"], 0.0))
        candidate["feedback_preview_score_adjustment"] = amount
        if candidate.get("base_score") is not None:
            candidate["feedback_preview_adjusted_score"] = round(float(candidate["base_score"]) + amount, 6)
    if sortable:
        ranked.sort(
            key=lambda c: (
                float(c.get("feedback_preview_adjusted_score") if c.get("feedback_preview_adjusted_score") is not None else c["base_score"]),
                -int(c["original_rank"]),
            ),
            reverse=True,
        )
    return ranked


def _apply_feature(query: str, candidates: Sequence[Dict[str, Any]], profile: Dict[str, Any] | None, synonyms: Dict[str, Any]) -> List[Dict[str, Any]]:
    if profile is None:
        return [dict(candidate) for candidate in candidates]
    ranked, _summary = apply_feature_rerank(
        query,
        candidates,
        profile=profile,
        synonym_config=synonyms,
    )
    return ranked


def _rank_of(ranked: Sequence[Dict[str, Any]], expected_qa_id: str | None) -> int | None:
    if not expected_qa_id:
        return None
    for index, candidate in enumerate(ranked, start=1):
        if candidate.get("qa_id") == expected_qa_id:
            return index
    return None


def _mode_result(baseline_rank: int | None, mode_rank: int | None) -> str:
    if baseline_rank is None or mode_rank is None:
        return "missing_expected"
    if mode_rank < baseline_rank:
        return "improved"
    if mode_rank > baseline_rank:
        return "regressed"
    return "unchanged"


def _mode_metrics(results: Sequence[Dict[str, Any]], mode: str) -> Dict[str, int]:
    out = {
        "evaluated_cases": 0,
        "top1": 0,
        "top3": 0,
        "top5": 0,
        "missing_expected_count": 0,
    }
    for result in results:
        rank = result[_RANK_KEYS[mode]]
        if rank is None:
            out["missing_expected_count"] += 1
            continue
        out["evaluated_cases"] += 1
        out["top1"] += int(rank <= 1)
        out["top3"] += int(rank <= 3)
        out["top5"] += int(rank <= 5)
    return out


def _delta_metrics(results: Sequence[Dict[str, Any]], mode: str, baseline: Dict[str, int], mode_metrics: Dict[str, int]) -> Dict[str, int]:
    adjusted_candidates = set()
    out = {
        "top1_delta": mode_metrics["top1"] - baseline["top1"],
        "top3_delta": mode_metrics["top3"] - baseline["top3"],
        "top5_delta": mode_metrics["top5"] - baseline["top5"],
        "improvement_count": 0,
        "regression_count": 0,
        "unchanged_count": 0,
        "adjusted_case_count": 0,
        "adjusted_candidate_count": 0,
    }
    for result in results:
        mode_result = result["result_by_mode"][mode]
        if mode_result == "improved":
            out["improvement_count"] += 1
        elif mode_result == "regressed":
            out["regression_count"] += 1
        elif mode_result == "unchanged":
            out["unchanged_count"] += 1
        adjusted = result["adjusted_candidate_ids"].get(mode, [])
        if adjusted:
            out["adjusted_case_count"] += 1
            adjusted_candidates.update(adjusted)
    out["adjusted_candidate_count"] = len(adjusted_candidates)
    return out


def _feature_specific(results: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    metrics = {
        "synonym_boost_case_count": 0,
        "business_term_boost_case_count": 0,
        "negative_mismatch_case_count": 0,
        "negative_mismatch_demoted_expected_count": 0,
        "negative_mismatch_demoted_non_expected_count": 0,
    }
    for result in results:
        reasons = result.get("feature_reasons") or {}
        if reasons.get("synonym_boost"):
            metrics["synonym_boost_case_count"] += 1
        if reasons.get("business_term_boost"):
            metrics["business_term_boost_case_count"] += 1
        if reasons.get("negative_mismatch"):
            metrics["negative_mismatch_case_count"] += 1
        if result.get("negative_mismatch_demoted_expected"):
            metrics["negative_mismatch_demoted_expected_count"] += 1
        if result.get("negative_mismatch_demoted_non_expected"):
            metrics["negative_mismatch_demoted_non_expected_count"] += 1
    return metrics


def _adjusted_ids(ranked: Sequence[Dict[str, Any]], *, feature: bool = False, feedback: bool = False) -> List[str]:
    ids = []
    for candidate in ranked:
        if feedback and candidate.get("feedback_preview_score_adjustment") not in {None, 0, 0.0}:
            ids.append(str(candidate.get("qa_id")))
        if feature and candidate.get("feature_score_adjustment") not in {None, 0, 0.0}:
            ids.append(str(candidate.get("qa_id")))
    return sorted(set(ids))


def _case_summary(case: Dict[str, Any], adjustments: Dict[str, float], feature_profile: Dict[str, Any] | None, synonyms: Dict[str, Any]) -> Dict[str, Any]:
    baseline = _rank_baseline(case["candidates"])
    feedback = _apply_feedback(baseline, adjustments)
    feature = _apply_feature(case["query"], baseline, feature_profile, synonyms)
    combined = _apply_feature(case["query"], feedback, feature_profile, synonyms)

    ranks = {
        "baseline": _rank_of(baseline, case["expected_qa_id"]),
        "feedback_preview": _rank_of(feedback, case["expected_qa_id"]),
        "feature_rerank": _rank_of(feature, case["expected_qa_id"]),
        "combined_feedback_then_feature": _rank_of(combined, case["expected_qa_id"]),
    }
    result_by_mode = {
        mode: _mode_result(ranks["baseline"], ranks[mode])
        for mode in _MODES
    }
    feature_candidates = feature + combined
    synonym_boost = any((candidate.get("feature_synonym_overlap_score") or 0) > 0 for candidate in feature_candidates)
    business_boost = any((candidate.get("feature_business_term_overlap_score") or 0) > 0 for candidate in feature_candidates)
    mismatch_ids = {
        str(candidate.get("qa_id"))
        for candidate in feature_candidates
        if candidate.get("feature_negative_mismatch")
    }
    expected = case["expected_qa_id"]
    demoted_expected = bool(expected and expected in mismatch_ids and ranks["feature_rerank"] and ranks["baseline"] and ranks["feature_rerank"] > ranks["baseline"])
    demoted_non_expected = bool(mismatch_ids - {expected})

    return {
        "case_id": case["case_id"],
        "query": case["query"],
        "expected_qa_id": expected,
        "baseline_rank": ranks["baseline"],
        "feedback_preview_rank": ranks["feedback_preview"],
        "feature_rerank_rank": ranks["feature_rerank"],
        "combined_rank": ranks["combined_feedback_then_feature"],
        "baseline_top_candidate_id": baseline[0]["qa_id"] if baseline else None,
        "feedback_preview_top_candidate_id": feedback[0]["qa_id"] if feedback else None,
        "feature_rerank_top_candidate_id": feature[0]["qa_id"] if feature else None,
        "combined_top_candidate_id": combined[0]["qa_id"] if combined else None,
        "result_by_mode": result_by_mode,
        "adjusted_candidate_ids": {
            "feedback_preview": _adjusted_ids(feedback, feedback=True),
            "feature_rerank": _adjusted_ids(feature, feature=True),
            "combined_feedback_then_feature": sorted(
                set(_adjusted_ids(combined, feedback=True) + _adjusted_ids(combined, feature=True))
            ),
        },
        "feature_reasons": {
            "synonym_boost": synonym_boost,
            "business_term_boost": business_boost,
            "negative_mismatch": bool(mismatch_ids),
            "negative_mismatch_candidate_ids": sorted(mismatch_ids)[:20],
        },
        "negative_mismatch_demoted_expected": demoted_expected,
        "negative_mismatch_demoted_non_expected": demoted_non_expected,
    }


def evaluate_feature_rerank(
    *,
    cases_path: str | Path = DEFAULT_CASES,
    feedback_profile_path: str | Path = DEFAULT_FEEDBACK_PROFILE,
    feature_profile_path: str | Path = DEFAULT_FEATURE_PROFILE,
    synonyms_path: str | Path = DEFAULT_SYNONYMS,
    output: str | Path = DEFAULT_OUTPUT,
) -> Dict[str, Any]:
    rows, malformed, missing_cases = load_jsonl_safely(cases_path)
    feedback_adjustments, feedback_info, missing_feedback = _load_feedback_adjustments(feedback_profile_path)
    feature_profile, feature_info, missing_feature = _load_feature_profile(feature_profile_path)
    synonyms, missing_synonyms = _load_synonyms(synonyms_path)
    cases = [_normalize_case(row, index) for index, row in enumerate(rows)]
    summaries = [
        _case_summary(case, feedback_adjustments, feature_profile, synonyms)
        for case in cases
    ]

    metrics = {
        "baseline": _mode_metrics(summaries, "baseline"),
        "feedback_preview": _mode_metrics(summaries, "feedback_preview"),
        "feature_rerank": _mode_metrics(summaries, "feature_rerank"),
        "combined_feedback_then_feature": _mode_metrics(summaries, "combined_feedback_then_feature"),
    }
    deltas = {
        mode: _delta_metrics(summaries, mode, metrics["baseline"], metrics[mode])
        for mode in _MODES
    }
    missing_files = []
    if missing_cases:
        missing_files.append(str(cases_path))
    if missing_feedback:
        missing_files.append(str(feedback_profile_path))
    if missing_feature:
        missing_files.append(str(feature_profile_path))
    if missing_synonyms:
        missing_files.append(str(synonyms_path))

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_paths": {
            "cases": str(cases_path),
            "feedback_preview_profile": str(feedback_profile_path),
            "feature_rerank_profile": str(feature_profile_path),
            "japanese_synonyms": str(synonyms_path),
        },
        "profile_info": {
            "feedback_preview": feedback_info,
            "feature_rerank": feature_info,
        },
        "metrics": metrics,
        "deltas": deltas,
        "feature_specific_metrics": _feature_specific(summaries),
        "improvements": [
            summary for summary in summaries if any(summary["result_by_mode"][mode] == "improved" for mode in _MODES)
        ],
        "regressions": [
            summary for summary in summaries if any(summary["result_by_mode"][mode] == "regressed" for mode in _MODES)
        ],
        "cases": summaries,
        "data_quality": {
            "skipped_malformed_lines": malformed,
            "missing_expected_count": metrics["baseline"]["missing_expected_count"],
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
        description="Compare baseline, feedback_preview, Japanese feature, and combined approved_similar rerank modes."
    )
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--feedback-profile", default=str(DEFAULT_FEEDBACK_PROFILE))
    parser.add_argument("--feature-profile", default=str(DEFAULT_FEATURE_PROFILE))
    parser.add_argument("--synonyms", default=str(DEFAULT_SYNONYMS))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)

    report = evaluate_feature_rerank(
        cases_path=args.cases,
        feedback_profile_path=args.feedback_profile,
        feature_profile_path=args.feature_profile,
        synonyms_path=args.synonyms,
        output=args.output,
    )
    print(
        json.dumps(
            {
                "output_path": str(args.output),
                "metrics": report["metrics"],
                "deltas": report["deltas"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
