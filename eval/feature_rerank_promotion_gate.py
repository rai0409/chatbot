from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


DEFAULT_INPUT = Path("artifacts/eval/feature_rerank_comparison.json")
DEFAULT_OUTPUT = Path("artifacts/eval/feature_rerank_promotion_decision.json")
TARGET_MODES = ("feature_rerank", "combined_feedback_then_feature")
DEFAULT_THRESHOLD_PROFILE = {
    "profile_name": "conservative_feature_rerank_promotion_gate",
    "min_cases": 10,
    "min_top1_delta": 0.0,
    "min_top3_delta": 0.0,
    "min_top5_delta": 0.0,
    "max_regression_count": 0,
    "max_regression_rate": 0.05,
    "require_no_negative_mismatch_safety_regression": True,
}
_REQUIRED_METRICS = (
    "evaluated_cases",
    "top1_delta",
    "top3_delta",
    "top5_delta",
    "regression_count",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _number(value: Any, default: float | None = None) -> float | None:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    number = _number(value)
    if number is None:
        return default
    return int(number)


def _thresholds(threshold_profile: dict[str, Any] | None) -> dict[str, Any]:
    thresholds = dict(DEFAULT_THRESHOLD_PROFILE)
    if isinstance(threshold_profile, dict):
        for key in DEFAULT_THRESHOLD_PROFILE:
            if key in threshold_profile:
                thresholds[key] = threshold_profile[key]
    thresholds["min_cases"] = max(0, _int(thresholds.get("min_cases"), 10))
    thresholds["min_top1_delta"] = _number(thresholds.get("min_top1_delta"), 0.0) or 0.0
    thresholds["min_top3_delta"] = _number(thresholds.get("min_top3_delta"), 0.0) or 0.0
    thresholds["min_top5_delta"] = _number(thresholds.get("min_top5_delta"), 0.0) or 0.0
    thresholds["max_regression_count"] = max(0, _int(thresholds.get("max_regression_count"), 0))
    thresholds["max_regression_rate"] = max(0.0, _number(thresholds.get("max_regression_rate"), 0.05) or 0.0)
    thresholds["require_no_negative_mismatch_safety_regression"] = bool(
        thresholds.get("require_no_negative_mismatch_safety_regression", True)
    )
    return thresholds


def _safe_load_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, "input_missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None, "input_malformed"
    if not isinstance(payload, dict):
        return None, "input_not_object"
    return payload, None


def _mode_source(comparison: dict[str, Any], mode: str) -> dict[str, Any]:
    metrics = comparison.get("metrics")
    if isinstance(metrics, dict) and isinstance(metrics.get(mode), dict):
        return dict(metrics[mode])
    if isinstance(comparison.get(mode), dict):
        return dict(comparison[mode])
    return {}


def _delta_source(comparison: dict[str, Any], mode: str) -> dict[str, Any]:
    deltas = comparison.get("deltas")
    if isinstance(deltas, dict) and isinstance(deltas.get(mode), dict):
        return dict(deltas[mode])
    source = _mode_source(comparison, mode)
    return {key: source.get(key) for key in ("top1_delta", "top3_delta", "top5_delta", "improvement_count", "regression_count")}


def _baseline_source(comparison: dict[str, Any]) -> dict[str, Any]:
    metrics = comparison.get("metrics")
    if isinstance(metrics, dict) and isinstance(metrics.get("baseline"), dict):
        return dict(metrics["baseline"])
    if isinstance(comparison.get("baseline"), dict):
        return dict(comparison["baseline"])
    return {}


def _feature_specific_source(comparison: dict[str, Any], mode: str) -> dict[str, Any]:
    source = comparison.get("feature_specific_metrics")
    if not isinstance(source, dict):
        return {}
    if isinstance(source.get(mode), dict):
        return dict(source[mode])
    return dict(source)


def _extract_mode_metrics(comparison: dict[str, Any], mode: str) -> tuple[dict[str, Any], list[str]]:
    mode_metrics = _mode_source(comparison, mode)
    baseline = _baseline_source(comparison)
    deltas = _delta_source(comparison, mode)
    feature_specific = _feature_specific_source(comparison, mode)
    missing: list[str] = []

    evaluated_cases = _number(
        mode_metrics.get("evaluated_cases", mode_metrics.get("total_cases", baseline.get("evaluated_cases")))
    )
    if evaluated_cases is None:
        missing.append("evaluated_cases")
        evaluated_cases = 0.0

    metrics: dict[str, Any] = {
        "evaluated_cases": int(evaluated_cases),
        "top1": _number(mode_metrics.get("top1")),
        "top3": _number(mode_metrics.get("top3")),
        "top5": _number(mode_metrics.get("top5")),
        "baseline_top1": _number(baseline.get("top1")),
        "baseline_top3": _number(baseline.get("top3")),
        "baseline_top5": _number(baseline.get("top5")),
        "top1_delta": _number(deltas.get("top1_delta", mode_metrics.get("top1_delta"))),
        "top3_delta": _number(deltas.get("top3_delta", mode_metrics.get("top3_delta"))),
        "top5_delta": _number(deltas.get("top5_delta", mode_metrics.get("top5_delta"))),
        "improvement_count": _int(deltas.get("improvement_count", mode_metrics.get("improvement_count"))),
        "regression_count": _int(deltas.get("regression_count", mode_metrics.get("regression_count"))),
        "synonym_boost_case_count": _int(feature_specific.get("synonym_boost_case_count")),
        "business_term_boost_case_count": _int(feature_specific.get("business_term_boost_case_count")),
        "negative_mismatch_case_count": _int(feature_specific.get("negative_mismatch_case_count")),
        "negative_mismatch_demoted_expected_count": _int(
            feature_specific.get("negative_mismatch_demoted_expected_count")
        ),
        "negative_mismatch_demoted_non_expected_count": _int(
            feature_specific.get("negative_mismatch_demoted_non_expected_count")
        ),
    }

    for key in ("top1_delta", "top3_delta", "top5_delta"):
        if metrics[key] is None:
            mode_value = metrics.get(key.replace("_delta", ""))
            baseline_value = metrics.get(f"baseline_{key.replace('_delta', '')}")
            if mode_value is not None and baseline_value is not None:
                metrics[key] = float(mode_value) - float(baseline_value)
            else:
                missing.append(key)

    if "regression_count" not in deltas and "regression_count" not in mode_metrics:
        missing.append("regression_count")

    regression_rate = 0.0
    if metrics["evaluated_cases"] > 0:
        regression_rate = float(metrics["regression_count"]) / float(metrics["evaluated_cases"])
    metrics["regression_rate"] = regression_rate
    return metrics, missing


def _recommended_scope(decision: str, metrics: dict[str, Any], thresholds: dict[str, Any]) -> str:
    if decision in {"insufficient_data", "blocked"}:
        return "none"
    if decision == "needs_review":
        return "evaluation"
    if (
        metrics.get("regression_count") == 0
        and metrics.get("regression_rate", 1.0) == 0
        and (metrics.get("top1_delta") or 0) > max(0.0, float(thresholds["min_top1_delta"]))
        and (metrics.get("top3_delta") or 0) >= 0
        and (metrics.get("top5_delta") or 0) >= 0
        and metrics.get("evaluated_cases", 0) >= max(int(thresholds["min_cases"]), 30)
    ):
        return "production_safe_candidate"
    return "pilot_high_accuracy"


def _decide_mode(
    mode: str,
    metrics: dict[str, Any],
    missing_metrics: list[str],
    thresholds: dict[str, Any],
    base_reasons: list[str] | None = None,
) -> dict[str, Any]:
    reasons = list(base_reasons or [])
    if missing_metrics:
        reasons.append("required_metrics_missing:" + ",".join(sorted(set(missing_metrics))))
    if metrics.get("evaluated_cases", 0) < int(thresholds["min_cases"]):
        reasons.append("evaluated_cases_below_min_cases")
    if reasons:
        decision = "insufficient_data"
        return _mode_report(mode, decision, reasons, metrics, thresholds)

    blocked_reasons: list[str] = []
    if (metrics.get("top1_delta") or 0) < float(thresholds["min_top1_delta"]):
        blocked_reasons.append("top1_delta_below_threshold")
    if (metrics.get("top3_delta") or 0) < float(thresholds["min_top3_delta"]):
        blocked_reasons.append("top3_delta_below_threshold")
    if (metrics.get("top5_delta") or 0) < float(thresholds["min_top5_delta"]):
        blocked_reasons.append("top5_delta_below_threshold")
    if int(metrics.get("regression_count") or 0) > int(thresholds["max_regression_count"]):
        blocked_reasons.append("regression_count_above_threshold")
    if float(metrics.get("regression_rate") or 0.0) > float(thresholds["max_regression_rate"]):
        blocked_reasons.append("regression_rate_above_threshold")
    if (
        thresholds["require_no_negative_mismatch_safety_regression"]
        and int(metrics.get("negative_mismatch_demoted_expected_count") or 0) > 0
    ):
        blocked_reasons.append("negative_mismatch_safety_regression")
    if blocked_reasons:
        return _mode_report(mode, "blocked", blocked_reasons, metrics, thresholds)

    review_reasons: list[str] = []
    if int(metrics.get("regression_count") or 0) > 0:
        review_reasons.append("non_zero_regressions")
    if int(metrics.get("improvement_count") or 0) > 0 and int(metrics.get("regression_count") or 0) > 0:
        review_reasons.append("mixed_improvements_and_regressions")
    if (
        mode == "combined_feedback_then_feature"
        and int(metrics.get("negative_mismatch_case_count") or 0) > 0
        and int(metrics.get("negative_mismatch_demoted_expected_count") or 0) > 0
    ):
        review_reasons.append("combined_mode_negative_mismatch_safety_concern")
    if review_reasons:
        return _mode_report(mode, "needs_review", review_reasons, metrics, thresholds)

    return _mode_report(mode, "promote_candidate", ["promotion_candidate_thresholds_met"], metrics, thresholds)


def _mode_report(
    mode: str,
    decision: str,
    reasons: list[str],
    metrics: dict[str, Any],
    thresholds: dict[str, Any],
) -> dict[str, Any]:
    return {
        "mode": mode,
        "decision": decision,
        "reasons": reasons,
        "metrics_used": metrics,
        "threshold_profile": thresholds,
        "recommended_profile_scope": _recommended_scope(decision, metrics, thresholds),
        "runtime_enabled": False,
        "production_enabled": False,
    }


def _empty_metrics() -> dict[str, Any]:
    return {
        "evaluated_cases": 0,
        "top1": None,
        "top3": None,
        "top5": None,
        "baseline_top1": None,
        "baseline_top3": None,
        "baseline_top5": None,
        "top1_delta": None,
        "top3_delta": None,
        "top5_delta": None,
        "improvement_count": 0,
        "regression_count": 0,
        "regression_rate": 0.0,
        "synonym_boost_case_count": 0,
        "business_term_boost_case_count": 0,
        "negative_mismatch_case_count": 0,
        "negative_mismatch_demoted_expected_count": 0,
        "negative_mismatch_demoted_non_expected_count": 0,
    }


def evaluate_feature_rerank_promotion(
    comparison: dict[str, Any],
    threshold_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    thresholds = _thresholds(threshold_profile)
    decisions = []
    warnings: list[str] = []
    for mode in TARGET_MODES:
        metrics, missing = _extract_mode_metrics(comparison if isinstance(comparison, dict) else {}, mode)
        if missing:
            warnings.append(f"{mode}:missing_metrics:{','.join(sorted(set(missing)))}")
        decisions.append(_decide_mode(mode, metrics, missing, thresholds))

    summary = _summary(decisions)
    return {
        "generated_at": _utc_now(),
        "input_path": None,
        "output_path": None,
        "threshold_profile": thresholds,
        "runtime_enabled": False,
        "production_enabled": False,
        "decisions": decisions,
        "summary": summary,
        "warnings": warnings,
    }


def _summary(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {
        "promote_candidate_count": 0,
        "blocked_count": 0,
        "needs_review_count": 0,
        "insufficient_data_count": 0,
    }
    for decision in decisions:
        key = f"{decision.get('decision')}_count"
        if key in counts:
            counts[key] += 1
    return {"evaluated_modes": [decision.get("mode") for decision in decisions], **counts}


def _safe_insufficient_report(
    *,
    input_path: Path,
    output_path: Path,
    thresholds: dict[str, Any],
    warning: str,
) -> dict[str, Any]:
    decisions = [
        _mode_report(mode, "insufficient_data", [warning], _empty_metrics(), thresholds)
        for mode in TARGET_MODES
    ]
    return {
        "generated_at": _utc_now(),
        "input_path": str(input_path),
        "output_path": str(output_path),
        "threshold_profile": thresholds,
        "runtime_enabled": False,
        "production_enabled": False,
        "decisions": decisions,
        "summary": _summary(decisions),
        "warnings": [warning],
    }


def build_feature_rerank_promotion_decision(
    input_path: str | Path = DEFAULT_INPUT,
    output_path: str | Path = DEFAULT_OUTPUT,
    threshold_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    input_path = Path(input_path)
    output_path = Path(output_path)
    thresholds = _thresholds(threshold_profile)
    comparison, error = _safe_load_json(input_path)
    if error:
        report = _safe_insufficient_report(
            input_path=input_path,
            output_path=output_path,
            thresholds=thresholds,
            warning=error,
        )
    else:
        report = evaluate_feature_rerank_promotion(comparison or {}, thresholds)
        report["input_path"] = str(input_path)
        report["output_path"] = str(output_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build an offline promotion decision report for approved_similar feature rerank modes."
    )
    parser.add_argument("--input", "--comparison", dest="input_path", default=str(DEFAULT_INPUT))
    parser.add_argument("--output", dest="output_path", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--min-cases", type=int, default=DEFAULT_THRESHOLD_PROFILE["min_cases"])
    parser.add_argument("--min-top1-delta", type=float, default=DEFAULT_THRESHOLD_PROFILE["min_top1_delta"])
    parser.add_argument("--min-top3-delta", type=float, default=DEFAULT_THRESHOLD_PROFILE["min_top3_delta"])
    parser.add_argument("--min-top5-delta", type=float, default=DEFAULT_THRESHOLD_PROFILE["min_top5_delta"])
    parser.add_argument("--max-regression-count", type=int, default=DEFAULT_THRESHOLD_PROFILE["max_regression_count"])
    parser.add_argument("--max-regression-rate", type=float, default=DEFAULT_THRESHOLD_PROFILE["max_regression_rate"])
    parser.add_argument(
        "--allow-negative-mismatch-safety-regression",
        action="store_true",
        help="Do not block solely on negative_mismatch_demoted_expected_count.",
    )
    args = parser.parse_args(argv)

    threshold_profile = {
        "min_cases": args.min_cases,
        "min_top1_delta": args.min_top1_delta,
        "min_top3_delta": args.min_top3_delta,
        "min_top5_delta": args.min_top5_delta,
        "max_regression_count": args.max_regression_count,
        "max_regression_rate": args.max_regression_rate,
        "require_no_negative_mismatch_safety_regression": not args.allow_negative_mismatch_safety_regression,
    }
    report = build_feature_rerank_promotion_decision(
        input_path=args.input_path,
        output_path=args.output_path,
        threshold_profile=threshold_profile,
    )
    print(
        json.dumps(
            {
                "output_path": report["output_path"],
                "summary": report["summary"],
                "decisions": [
                    {
                        "mode": decision["mode"],
                        "decision": decision["decision"],
                        "recommended_profile_scope": decision["recommended_profile_scope"],
                    }
                    for decision in report["decisions"]
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
