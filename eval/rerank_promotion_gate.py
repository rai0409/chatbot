from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Sequence, Tuple


DEFAULT_COMPARISON = Path("artifacts/eval/feedback_preview_comparison.json")
DEFAULT_PROFILE = Path("configs/approved_similar_rerank_weights_feedback_preview.json")
DEFAULT_OUTPUT = Path("artifacts/eval/rerank_promotion_decision.json")

DEFAULT_THRESHOLDS = {
    "min_evaluated_cases": 1,
    "min_top1_delta": 0,
    "max_allowed_regressions": 1,
    "max_missing_expected_count": 0,
}
_METRIC_KEYS = (
    "evaluated_cases",
    "baseline_top1",
    "preview_top1",
    "top1_delta",
    "baseline_top3",
    "preview_top3",
    "top3_delta",
    "baseline_top5",
    "preview_top5",
    "top5_delta",
    "improvement_count",
    "regression_count",
    "missing_expected_count",
)


def _int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _safe_load_json(path: str | Path) -> Tuple[Dict[str, Any] | None, str | None]:
    input_path = Path(path)
    if not input_path.exists():
        return None, "missing"
    try:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
    except Exception:
        return None, "invalid_json"
    if not isinstance(payload, dict):
        return None, "not_object"
    return payload, None


def _extract_metrics(comparison: Dict[str, Any] | None) -> Dict[str, int]:
    source: Dict[str, Any] = {}
    if isinstance(comparison, dict):
        nested = comparison.get("metrics")
        source = nested if isinstance(nested, dict) else comparison
    return {key: _int_value(source.get(key)) for key in _METRIC_KEYS}


def _profile_info(profile: Dict[str, Any] | None, error: str | None) -> Dict[str, Any]:
    info = {
        "loaded": profile is not None,
        "valid": False,
        "profile_name": None,
        "profile_type": None,
        "production_enabled": None,
        "runtime_enabled": None,
        "reason": error,
    }
    if profile is None:
        return info

    info.update(
        {
            "profile_name": profile.get("profile_name"),
            "profile_type": profile.get("profile_type"),
            "production_enabled": profile.get("production_enabled"),
            "runtime_enabled": profile.get("runtime_enabled"),
        }
    )
    safety = profile.get("safety")
    if profile.get("production_enabled") is not False:
        info["reason"] = "production_enabled_must_be_false"
    elif profile.get("profile_name") is not None and profile.get("profile_name") != "feedback_preview":
        info["reason"] = "invalid_profile_name"
    elif profile.get("profile_type") is not None and profile.get("profile_type") != "approved_similar_feedback_rerank":
        info["reason"] = "invalid_profile_type"
    elif isinstance(safety, dict) and safety.get("no_runtime_ranking_change") is not None and safety.get("no_runtime_ranking_change") is not True:
        info["reason"] = "unsafe_runtime_ranking_flag"
    elif isinstance(safety, dict) and safety.get("no_auto_answer_enablement") is not None and safety.get("no_auto_answer_enablement") is not True:
        info["reason"] = "unsafe_auto_answer_flag"
    elif isinstance(safety, dict) and safety.get("requires_offline_evaluation_before_production") is not None and safety.get("requires_offline_evaluation_before_production") is not True:
        info["reason"] = "offline_evaluation_flag_missing"
    else:
        info["valid"] = True
        info["reason"] = None
    return info


def _decision(metrics: Dict[str, int], profile_info: Dict[str, Any], thresholds: Dict[str, int], *, comparison_missing: bool, profile_missing: bool) -> Tuple[str, bool, list[str]]:
    reasons: list[str] = []
    if comparison_missing:
        reasons.append("comparison_file_missing")
    if profile_missing:
        reasons.append("profile_file_missing")
    if metrics["evaluated_cases"] == 0:
        reasons.append("no_evaluated_cases")
    if comparison_missing or profile_missing or metrics["evaluated_cases"] == 0:
        return "insufficient_data", False, reasons

    if not profile_info.get("valid"):
        reasons.append(f"profile_invalid:{profile_info.get('reason') or 'unknown'}")
        return "blocked", False, reasons

    if metrics["evaluated_cases"] < thresholds["min_evaluated_cases"]:
        reasons.append("evaluated_cases_below_threshold")
    if metrics["preview_top3"] < metrics["baseline_top3"]:
        reasons.append("top3_regression")
    if metrics["preview_top5"] < metrics["baseline_top5"]:
        reasons.append("top5_regression")
    if metrics["regression_count"] > thresholds["max_allowed_regressions"]:
        reasons.append("regression_count_above_threshold")
    if metrics["top1_delta"] < thresholds["min_top1_delta"]:
        reasons.append("top1_delta_below_threshold")
    if metrics["missing_expected_count"] > thresholds["max_missing_expected_count"]:
        reasons.append("missing_expected_count_above_threshold")

    if reasons:
        return "blocked", False, reasons
    return "promote_candidate", True, ["promotion_gate_passed"]


def evaluate_rerank_promotion_gate(
    *,
    comparison_path: str | Path = DEFAULT_COMPARISON,
    profile_path: str | Path = DEFAULT_PROFILE,
    output: str | Path = DEFAULT_OUTPUT,
    min_evaluated_cases: int = DEFAULT_THRESHOLDS["min_evaluated_cases"],
    min_top1_delta: int = DEFAULT_THRESHOLDS["min_top1_delta"],
    max_allowed_regressions: int = DEFAULT_THRESHOLDS["max_allowed_regressions"],
    max_missing_expected_count: int = DEFAULT_THRESHOLDS["max_missing_expected_count"],
) -> Dict[str, Any]:
    thresholds = {
        "min_evaluated_cases": int(min_evaluated_cases),
        "min_top1_delta": int(min_top1_delta),
        "max_allowed_regressions": int(max_allowed_regressions),
        "max_missing_expected_count": int(max_missing_expected_count),
    }
    comparison, comparison_error = _safe_load_json(comparison_path)
    profile, profile_error = _safe_load_json(profile_path)
    metrics = _extract_metrics(comparison)
    profile_summary = _profile_info(profile, profile_error)

    missing_files = []
    invalid_files = []
    if comparison_error == "missing":
        missing_files.append(str(comparison_path))
    elif comparison_error:
        invalid_files.append(str(comparison_path))
    if profile_error == "missing":
        missing_files.append(str(profile_path))
    elif profile_error:
        invalid_files.append(str(profile_path))

    decision, safe_to_promote, reasons = _decision(
        metrics,
        profile_summary,
        thresholds,
        comparison_missing=comparison_error == "missing",
        profile_missing=profile_error == "missing",
    )
    if comparison_error and comparison_error != "missing":
        reasons.append(f"comparison_invalid:{comparison_error}")
    if profile_error and profile_error != "missing":
        reasons.append(f"profile_invalid:{profile_error}")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "decision": decision,
        "safe_to_promote": safe_to_promote,
        "reasons": reasons,
        "thresholds": thresholds,
        "metrics": metrics,
        "profile_info": profile_summary,
        "input_paths": {
            "comparison": str(comparison_path),
            "profile": str(profile_path),
        },
        "data_quality": {
            "missing_input_files": missing_files,
            "invalid_input_files": invalid_files,
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
        description="Gate feedback_preview approved_similar rerank promotion using offline comparison metrics."
    )
    parser.add_argument("--comparison", default=str(DEFAULT_COMPARISON))
    parser.add_argument("--profile", default=str(DEFAULT_PROFILE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--min-evaluated-cases", type=int, default=DEFAULT_THRESHOLDS["min_evaluated_cases"])
    parser.add_argument("--min-top1-delta", type=int, default=DEFAULT_THRESHOLDS["min_top1_delta"])
    parser.add_argument("--max-allowed-regressions", type=int, default=DEFAULT_THRESHOLDS["max_allowed_regressions"])
    parser.add_argument("--max-missing-expected-count", type=int, default=DEFAULT_THRESHOLDS["max_missing_expected_count"])
    args = parser.parse_args(argv)

    report = evaluate_rerank_promotion_gate(
        comparison_path=args.comparison,
        profile_path=args.profile,
        output=args.output,
        min_evaluated_cases=args.min_evaluated_cases,
        min_top1_delta=args.min_top1_delta,
        max_allowed_regressions=args.max_allowed_regressions,
        max_missing_expected_count=args.max_missing_expected_count,
    )
    print(
        json.dumps(
            {
                "output_path": str(args.output),
                "decision": report["decision"],
                "safe_to_promote": report["safe_to_promote"],
                "reasons": report["reasons"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
