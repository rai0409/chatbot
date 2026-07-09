from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence

from eval import approved_similar_decision_eval


DecisionEvalFn = Callable[..., Dict[str, Any]]


def _threshold_name(profile: str) -> str:
    if profile == "no_thresholds":
        return "no_thresholds"
    return Path(profile).stem


def _ids(items: Sequence[Dict[str, Any]]) -> List[Any]:
    return [item.get("id") for item in items]


def _summarize_threshold_profile(
    *,
    name: str,
    threshold_path: str | None,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    summary = dict(payload.get("summary") or {})
    threshold_info = dict(payload.get("threshold_info") or {})
    failed_high_confidence = list(summary.get("failed_high_confidence_cases") or [])
    numeric_not_blocked = list(summary.get("numeric_conflict_not_blocked_cases") or [])
    negation_not_reviewed = list(summary.get("negation_conflict_not_reviewed_cases") or [])
    passed_blocked = list(summary.get("passed_blocked_or_reviewed_cases") or [])
    return {
        "name": name,
        "threshold_path": threshold_path,
        "threshold_source": threshold_info.get("threshold_source"),
        "threshold_profile_name": threshold_info.get("threshold_profile_name"),
        "threshold_profile_path": threshold_info.get("threshold_profile_path"),
        "total": int(summary.get("total") or 0),
        "passed": int(summary.get("passed") or 0),
        "failed": int(summary.get("failed") or 0),
        "pass_rate": float(summary.get("pass_rate") or 0.0),
        "route_counts": dict(summary.get("route_counts") or {}),
        "high_confidence_count": int(summary.get("high_confidence_count") or 0),
        "candidate_only_count": int(summary.get("candidate_only_count") or 0),
        "blocked_or_review_count": int(summary.get("blocked_or_review_count") or 0),
        "low_confidence_count": int(summary.get("low_confidence_count") or 0),
        "failed_high_confidence_count": len(failed_high_confidence),
        "failed_high_confidence_ids": _ids(failed_high_confidence),
        "numeric_conflict_not_blocked_count": len(numeric_not_blocked),
        "numeric_conflict_not_blocked_ids": _ids(numeric_not_blocked),
        "negation_conflict_not_reviewed_count": len(negation_not_reviewed),
        "negation_conflict_not_reviewed_ids": _ids(negation_not_reviewed),
        "passed_blocked_or_reviewed_count": len(passed_blocked),
        "passed_blocked_or_reviewed_ids": _ids(passed_blocked),
    }


def compare_decision_threshold_profiles(
    *,
    cases: str | Path,
    collection: str | None,
    keyword_profile: str | None,
    threshold_profiles: Sequence[str],
    top_k: int = 5,
    output_json: str | Path | None = None,
    output_md: str | Path | None = None,
    decision_eval_fn: DecisionEvalFn = approved_similar_decision_eval.run_decision_eval,
) -> Dict[str, Any]:
    if not threshold_profiles:
        raise ValueError("at least one threshold profile is required")

    previous_env = os.environ.get("APPROVED_SIMILAR_DECISION_THRESHOLDS")
    previous_present = "APPROVED_SIMILAR_DECISION_THRESHOLDS" in os.environ
    profiles: List[Dict[str, Any]] = []
    try:
        for threshold_profile in threshold_profiles:
            threshold_path = None if threshold_profile == "no_thresholds" else str(threshold_profile)
            payload = decision_eval_fn(
                cases=cases,
                collection=collection,
                profile=keyword_profile,
                thresholds=threshold_path,
                top_k=top_k,
                output_json=None,
                output_md=None,
            )
            profiles.append(
                _summarize_threshold_profile(
                    name=_threshold_name(threshold_profile),
                    threshold_path=threshold_path,
                    payload=payload,
                )
            )
    finally:
        if previous_present:
            os.environ["APPROVED_SIMILAR_DECISION_THRESHOLDS"] = str(previous_env)
        else:
            os.environ.pop("APPROVED_SIMILAR_DECISION_THRESHOLDS", None)

    report = {
        "cases": str(cases),
        "collection": collection,
        "keyword_profile": keyword_profile,
        "top_k": top_k,
        "threshold_profiles": profiles,
    }
    if output_json is not None:
        output_path = Path(output_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if output_md is not None:
        output_path = Path(output_md)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(render_markdown(report), encoding="utf-8")
    return report


def render_markdown(report: Dict[str, Any]) -> str:
    lines = [
        "# approved_similar_candidate Decision Threshold Profile Comparison",
        "",
        f"- cases: `{report.get('cases')}`",
        f"- collection: `{report.get('collection')}`",
        f"- keyword_profile: `{report.get('keyword_profile')}`",
        f"- top_k: `{report.get('top_k')}`",
        "",
        "## Summary",
        "",
        "| threshold_profile | passed | failed | pass_rate | high_conf | candidate_only | blocked_or_review | low_conf | failed_high_conf | passed_blocked_or_reviewed |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for profile in report.get("threshold_profiles") or []:
        lines.append(
            "| {name} | {passed} | {failed} | {pass_rate:.3f} | {high} | {candidate} | {blocked} | {low} | {failed_high} | {passed_blocked} |".format(
                name=profile.get("name"),
                passed=profile.get("passed"),
                failed=profile.get("failed"),
                pass_rate=float(profile.get("pass_rate") or 0.0),
                high=profile.get("high_confidence_count"),
                candidate=profile.get("candidate_only_count"),
                blocked=profile.get("blocked_or_review_count"),
                low=profile.get("low_confidence_count"),
                failed_high=profile.get("failed_high_confidence_count"),
                passed_blocked=profile.get("passed_blocked_or_reviewed_count"),
            )
        )
    lines.extend(["", "## Failed High Confidence Cases", ""])
    any_failed_high = False
    for profile in report.get("threshold_profiles") or []:
        ids = profile.get("failed_high_confidence_ids") or []
        if ids:
            any_failed_high = True
            lines.append(f"- {profile.get('name')}: {', '.join(str(item) for item in ids)}")
    if not any_failed_high:
        lines.append("(none)")
    lines.extend(["", "## Safety Highlights", ""])
    for profile in report.get("threshold_profiles") or []:
        lines.append(
            "- {name}: numeric_not_blocked={numeric} negation_not_reviewed={negation} passed_blocked_or_reviewed={passed_blocked}".format(
                name=profile.get("name"),
                numeric=profile.get("numeric_conflict_not_blocked_count"),
                negation=profile.get("negation_conflict_not_reviewed_count"),
                passed_blocked=profile.get("passed_blocked_or_reviewed_count"),
            )
        )
    return "\n".join(lines).rstrip() + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare approved_similar decision threshold profiles.")
    parser.add_argument("--cases", required=True)
    parser.add_argument("--collection", default=None)
    parser.add_argument("--keyword-profile", default=None)
    parser.add_argument("--threshold-profiles", nargs="+", required=True)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    args = parser.parse_args(argv)

    compare_decision_threshold_profiles(
        cases=args.cases,
        collection=args.collection,
        keyword_profile=args.keyword_profile,
        threshold_profiles=args.threshold_profiles,
        top_k=args.top_k,
        output_json=args.output_json,
        output_md=args.output_md,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
