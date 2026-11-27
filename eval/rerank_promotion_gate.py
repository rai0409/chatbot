"""Promotion gate for a reranker (or any retrieval change) from paired eval.bench comparisons.

Default (statistical) gate: consumes the output of `python -m eval.bench.compare_runs` (two JSON files, or the run
directories, in which case compare_runs is called here with --require-same-conditions) and promotes only when ALL hold:

  conditions   both runs measured on the same eval set, corpus, split, match mode, embedding profile (compare_runs)
  min_cases    at least 100 paired answerable queries (configurable, never below 100 without --allow-small)
  ndcg         the paired cluster-bootstrap 95% CI of delta nDCG@10 (candidate - baseline) has a lower bound > 0
  hit@1        exact McNemar on hit@1 does not show the candidate significantly worse (alpha 0.05)
  latency      candidate total p95 (summary.jsonl of the candidate run) within --max-p95-ms, and the increase over
               the baseline within --max-p95-increase-ms when given

  python -m eval.bench.compare_runs --baseline-run runs/bench/<A> --baseline-config <id> \
      --candidate-run runs/bench/<B> --candidate-config <id> --metric ndcg@10 --require-same-conditions --out ndcg.json
  python -m eval.bench.compare_runs ... --metric hit@1 --require-same-conditions --out hit1.json
  python -m eval.rerank_promotion_gate --compare-ndcg ndcg.json --compare-hit1 hit1.json --max-p95-ms 1500 \
      --output artifacts/eval/rerank_promotion_decision.json
  # or let the gate call compare_runs itself:
  python -m eval.rerank_promotion_gate --baseline-run runs/bench/<A> --baseline-config <id> \
      --candidate-run runs/bench/<B> --candidate-config <id> --max-p95-ms 1500

Several candidates against ONE baseline (repeat --candidate-config): testing each at alpha 0.05 inflates the false
promotion rate, so --primary <config_id> is required (the one pre-registered comparison, gated as above); the other
candidates are reported as "exploratory" (never promotable, safe_to_promote=false) with their numbers for information.
--resamples must be >= 1000.

Exit 0 only for "promote_candidate". A single run directory holding both configs (one eval.bench grid) is fine:
pass the same directory as baseline and candidate run.

Legacy (--legacy-feedback-preview): the old feedback_preview approved_similar gate on top1/top3/top5 counts
(evaluate_rerank_promotion_gate). Its defaults (1 case, 1 allowed regression) are not a statistical test; it is
kept only for the offline feedback_preview profile workflow and must not be used to promote a reranker.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


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


# --strict: the production gate. Enough cases, a significant improvement
# (exact McNemar on improved vs regressed cases) and no regression at all.
STRICT_THRESHOLDS = {
    "min_evaluated_cases": 50,
    "min_top1_delta": 1,
    "max_allowed_regressions": 0,
    "max_missing_expected_count": 0,
}
STRICT_ALPHA = 0.05


def _mcnemar_p(improved: int, regressed: int) -> float:
    from eval.bench.stats import mcnemar_exact

    return float(mcnemar_exact(regressed, improved))


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
    strict: bool = False,
) -> Dict[str, Any]:
    if strict:
        min_evaluated_cases = max(int(min_evaluated_cases), STRICT_THRESHOLDS["min_evaluated_cases"])
        min_top1_delta = max(int(min_top1_delta), STRICT_THRESHOLDS["min_top1_delta"])
        max_allowed_regressions = STRICT_THRESHOLDS["max_allowed_regressions"]
        max_missing_expected_count = STRICT_THRESHOLDS["max_missing_expected_count"]
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
    significance = None
    if strict and decision == "promote_candidate":
        significance = _mcnemar_p(metrics["improvement_count"], metrics["regression_count"])
        if significance >= STRICT_ALPHA:
            decision, safe_to_promote = "blocked", False
            reasons = [f"improvement_not_significant:p={significance:.4f}"]
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
        "strict": bool(strict),
        "mcnemar_p": significance,
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


def legacy_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="(legacy) Gate feedback_preview approved_similar rerank promotion using offline comparison metrics."
    )
    parser.add_argument("--legacy-feedback-preview", action="store_true")
    parser.add_argument("--comparison", default=str(DEFAULT_COMPARISON))
    parser.add_argument("--profile", default=str(DEFAULT_PROFILE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--min-evaluated-cases", type=int, default=DEFAULT_THRESHOLDS["min_evaluated_cases"])
    parser.add_argument("--min-top1-delta", type=int, default=DEFAULT_THRESHOLDS["min_top1_delta"])
    parser.add_argument("--max-allowed-regressions", type=int, default=DEFAULT_THRESHOLDS["max_allowed_regressions"])
    parser.add_argument("--max-missing-expected-count", type=int, default=DEFAULT_THRESHOLDS["max_missing_expected_count"])
    parser.add_argument(
        "--strict",
        action="store_true",
        help="production gate: >= 50 cases, no regression, significant improvement; exit 1 unless promotable",
    )
    args = parser.parse_args(argv)

    report = evaluate_rerank_promotion_gate(
        comparison_path=args.comparison,
        profile_path=args.profile,
        output=args.output,
        min_evaluated_cases=args.min_evaluated_cases,
        min_top1_delta=args.min_top1_delta,
        max_allowed_regressions=args.max_allowed_regressions,
        max_missing_expected_count=args.max_missing_expected_count,
        strict=args.strict,
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
    if args.strict and not report["safe_to_promote"]:
        return 1
    return 0


# --- statistical gate (default) -------------------------------------------------------------------------------

OVERALL = "(all)"
STAT_MIN_CASES = 100


@dataclass(frozen=True)
class GatePolicy:
    min_cases: int = STAT_MIN_CASES  # paired answerable queries; below 100 a CI on nDCG@10 is too wide to promote on
    alpha: float = 0.05  # McNemar level; the nDCG CI level comes from compare_runs (its --alpha, default 0.05)
    max_candidate_p95_ms: Optional[float] = None  # absolute budget of the candidate's total p95
    max_p95_increase_ms: Optional[float] = None  # allowed increase of total p95 over the baseline
    require_same_conditions: bool = True


def _check(name: str, status: str, **detail: Any) -> Dict[str, Any]:
    return {"name": name, "status": status, **detail}


def _overall(comparison: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return ((comparison or {}).get("results") or {}).get(OVERALL) or {}


def _total_p95(run_dir: Optional[str], config_id: Optional[str]) -> Optional[float]:
    if not run_dir or not config_id:
        return None
    try:
        from eval.bench.report import load_summaries

        for row in load_summaries(run_dir):
            if row.get("config_id") == config_id:
                value = row.get("total_p95_ms")
                return float(value) if value is not None else None
    except (FileNotFoundError, ValueError):
        return None
    return None


def evaluate_statistical_gate(
    ndcg_comparison: Optional[Dict[str, Any]],
    hit1_comparison: Optional[Dict[str, Any]],
    policy: GatePolicy = GatePolicy(),
    *,
    baseline_p95_ms: Optional[float] = None,
    candidate_p95_ms: Optional[float] = None,
) -> Dict[str, Any]:
    """Decision from two compare_runs reports (metric ndcg@10 and metric hit@1). Latencies default to the runs' summaries."""
    checks: List[Dict[str, Any]] = []
    if not ndcg_comparison or not hit1_comparison:
        checks.append(_check("inputs", "insufficient", missing=[n for n, c in (("ndcg@10", ndcg_comparison), ("hit@1", hit1_comparison)) if not c]))
    else:
        metrics = (ndcg_comparison.get("metric"), hit1_comparison.get("metric"))
        ok = metrics == ("ndcg@10", "hit@1")
        same_pair = (ndcg_comparison.get("baseline"), ndcg_comparison.get("candidate")) == (
            hit1_comparison.get("baseline"), hit1_comparison.get("candidate"))
        checks.append(_check("inputs", "pass" if ok and same_pair else "fail", metrics=list(metrics), same_runs=same_pair))

    conditions = (ndcg_comparison or {}).get("conditions") or {}
    if policy.require_same_conditions:
        if not conditions:
            checks.append(_check("same_conditions", "insufficient", reason="comparison has no conditions block"))
        else:
            checks.append(_check("same_conditions", "pass" if conditions.get("comparable") else "fail",
                                 differences=conditions.get("differences"), unknown=conditions.get("unknown")))

    n = int((ndcg_comparison or {}).get("paired_queries") or 0)
    checks.append(_check("min_cases", "pass" if n >= policy.min_cases else "insufficient", n=n, needed=policy.min_cases))

    ndcg = _overall(ndcg_comparison)
    if ndcg and n >= policy.min_cases:
        low = ndcg.get("low")
        checks.append(_check("ndcg_at_10_improves", "pass" if low is not None and low > 0 else "fail",
                             diff=ndcg.get("diff"), low=low, high=ndcg.get("high"),
                             baseline=ndcg.get("baseline_mean"), candidate=ndcg.get("candidate_mean")))
    else:
        checks.append(_check("ndcg_at_10_improves", "insufficient"))

    hit = _overall(hit1_comparison)
    mcnemar = hit.get("mcnemar") or {}
    complete = all(mcnemar.get(k) is not None for k in ("baseline_only", "candidate_only", "p_value"))
    if mcnemar and complete and n >= policy.min_cases:  # a missing p_value must never read as "not significant"
        worse = float(mcnemar["baseline_only"]) > float(mcnemar["candidate_only"])
        p_value = float(mcnemar["p_value"])
        status = "fail" if worse and p_value < policy.alpha else "pass"
        checks.append(_check("hit_at_1_not_worse", status, baseline_only=mcnemar.get("baseline_only"),
                             candidate_only=mcnemar.get("candidate_only"), p_value=p_value, alpha=policy.alpha))
    else:
        checks.append(_check("hit_at_1_not_worse", "insufficient"))

    if candidate_p95_ms is None and ndcg_comparison:
        candidate_p95_ms = _total_p95(ndcg_comparison.get("candidate", {}).get("run"), ndcg_comparison.get("candidate", {}).get("config_id"))
    if baseline_p95_ms is None and ndcg_comparison:
        baseline_p95_ms = _total_p95(ndcg_comparison.get("baseline", {}).get("run"), ndcg_comparison.get("baseline", {}).get("config_id"))
    if policy.max_candidate_p95_ms is None and policy.max_p95_increase_ms is None:
        checks.append(_check("latency_p95", "fail", reason="no latency budget configured (--max-p95-ms / --max-p95-increase-ms)"))
    elif candidate_p95_ms is None or (policy.max_p95_increase_ms is not None and baseline_p95_ms is None):
        checks.append(_check("latency_p95", "insufficient", baseline_p95_ms=baseline_p95_ms, candidate_p95_ms=candidate_p95_ms))
    else:
        within = policy.max_candidate_p95_ms is None or candidate_p95_ms <= policy.max_candidate_p95_ms
        increase = None if baseline_p95_ms is None else candidate_p95_ms - baseline_p95_ms
        if policy.max_p95_increase_ms is not None and increase is not None and increase > policy.max_p95_increase_ms:
            within = False
        checks.append(_check("latency_p95", "pass" if within else "fail", baseline_p95_ms=baseline_p95_ms,
                             candidate_p95_ms=candidate_p95_ms, increase_ms=increase,
                             max_candidate_p95_ms=policy.max_candidate_p95_ms, max_p95_increase_ms=policy.max_p95_increase_ms))

    statuses = {c["status"] for c in checks}
    decision = "blocked" if "fail" in statuses else "insufficient_data" if "insufficient" in statuses else "promote_candidate"
    verdict = (ndcg_comparison or {}).get("verdict") or {}
    return {
        "schema": "rerank-promotion-gate-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "decision": decision,
        "safe_to_promote": decision == "promote_candidate",
        "reasons": [c["name"] for c in checks if c["status"] != "pass"] or ["promotion_gate_passed"],
        "checks": checks,
        "policy": asdict(policy),
        "baseline": (ndcg_comparison or {}).get("baseline"),
        "candidate": (ndcg_comparison or {}).get("candidate"),
        "info": {"non_inferior_all_slices_ndcg": verdict.get("non_inferior_all_slices"),
                 "slices_skipped_small": (ndcg_comparison or {}).get("slices_skipped_small")},
    }


def _resamples_arg(value: str) -> int:
    from eval.bench.stats import resamples_arg

    return resamples_arg(value)


def mark_exploratory(report: Dict[str, Any]) -> Dict[str, Any]:
    """A non-primary comparison: its numbers are kept, but it can never promote (multiple-comparison control)."""
    out = dict(report)
    out["raw_decision"] = out.get("decision")
    out["decision"] = "exploratory"
    out["safe_to_promote"] = False
    out["reasons"] = ["exploratory_not_primary"]
    return out


def _load_optional(path: Optional[str]) -> Optional[Dict[str, Any]]:
    if not path:
        return None
    payload, error = _safe_load_json(path)
    if error:
        raise SystemExit(f"cannot read {path}: {error}")
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--legacy-feedback-preview" in argv:
        return legacy_main(argv)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--legacy-feedback-preview", action="store_true", help="the old feedback_preview count gate")
    parser.add_argument("--compare-ndcg", help="compare_runs JSON with --metric ndcg@10")
    parser.add_argument("--compare-hit1", help="compare_runs JSON with --metric hit@1")
    parser.add_argument("--baseline-run")
    parser.add_argument("--baseline-config")
    parser.add_argument("--candidate-run")
    parser.add_argument("--candidate-config", action="append", default=None,
                        help="repeat for several candidates against the one baseline (then --primary is required)")
    parser.add_argument("--primary", default=None,
                        help="the pre-registered candidate config id when several are compared; the others are exploratory")
    parser.add_argument("--resamples", type=_resamples_arg, default=2000, help="bootstrap resamples, at least 1000")
    parser.add_argument("--min-cases", type=int, default=STAT_MIN_CASES)
    parser.add_argument("--allow-small", action="store_true", help="tests only: accept --min-cases below 100")
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--max-p95-ms", type=float, default=None)
    parser.add_argument("--max-p95-increase-ms", type=float, default=None)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)
    if not 0 < args.alpha < 1:
        parser.error("--alpha must be in (0, 1)")
    if args.min_cases < STAT_MIN_CASES and not args.allow_small:
        parser.error(f"--min-cases below {STAT_MIN_CASES} needs --allow-small")
    policy = GatePolicy(min_cases=args.min_cases, alpha=args.alpha, max_candidate_p95_ms=args.max_p95_ms,
                        max_p95_increase_ms=args.max_p95_increase_ms)
    exploratory: Dict[str, Any] = {}
    if args.baseline_run:
        from eval.bench.compare_runs import compare_runs

        candidates = list(args.candidate_config or [])
        if not (args.baseline_config and args.candidate_run and candidates):
            parser.error("--baseline-run/--baseline-config/--candidate-run/--candidate-config go together")
        primary = args.primary
        if len(candidates) > 1:
            if not primary:
                parser.error("several --candidate-config need --primary (the pre-registered comparison); the others are exploratory")
        elif primary is None:
            primary = candidates[0]
        if primary not in candidates:
            parser.error("--primary must be one of the --candidate-config values")
        report = None
        for candidate in candidates:
            pair = (args.baseline_run, args.baseline_config, args.candidate_run, candidate)
            ndcg = compare_runs(*pair, metric="ndcg@10", resamples=args.resamples, require_same_conditions=True)
            hit1 = compare_runs(*pair, metric="hit@1", resamples=args.resamples, require_same_conditions=True)
            result = evaluate_statistical_gate(ndcg, hit1, policy)
            if candidate == primary:
                report = result
            else:
                exploratory[candidate] = mark_exploratory(result)
        if exploratory:
            report["primary"] = primary
            report["exploratory"] = exploratory
            report["multiple_comparison_note"] = "one primary comparison is gated; the others are exploratory and cannot promote"
    else:
        ndcg, hit1 = _load_optional(args.compare_ndcg), _load_optional(args.compare_hit1)
        report = evaluate_statistical_gate(ndcg, hit1, policy)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"output_path": str(output), "decision": report["decision"], "reasons": report["reasons"]}, ensure_ascii=False, indent=2))
    return 0 if report["safe_to_promote"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
