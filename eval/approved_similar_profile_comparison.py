from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence

from eval import approved_similar_candidate_runner
from rag_core import approved_similar


EvalFn = Callable[..., Dict[str, Any]]


def _avg(values: Sequence[Any]) -> float | None:
    nums: List[float] = []
    for value in values:
        if value is None:
            continue
        try:
            nums.append(float(value))
        except (TypeError, ValueError):
            continue
    if not nums:
        return None
    return round(sum(nums) / len(nums), 6)


def _profile_name(profile: str) -> str:
    if profile == "no_profile":
        return "no_profile"
    return Path(profile).stem


def _failure_detail(case: Dict[str, Any]) -> Dict[str, Any]:
    top = case.get("top_candidate") or {}
    return {
        "id": case.get("id"),
        "category": case.get("category"),
        "query": case.get("query"),
        "expected_top_qa_id": case.get("expected_top_qa_id"),
        "expected_any_qa_ids": list(case.get("expected_any_qa_ids") or []),
        "actual_top_qa_id": case.get("actual_top_qa_id"),
        "top_answer_preview": case.get("top_answer_preview"),
        "generic_matched_terms": list(top.get("generic_matched_terms") or []),
        "specific_matched_terms": list(top.get("specific_matched_terms") or []),
        "weighted_keyword_score": top.get("weighted_keyword_score"),
        "failure_reasons": list(case.get("failure_reasons") or []),
    }


def _summarize_profile(*, name: str, profile_path: str | None, payload: Dict[str, Any]) -> Dict[str, Any]:
    cases = list(payload.get("per_case") or [])
    top_candidates = [case.get("top_candidate") or {} for case in cases]
    failed_cases = [case for case in cases if not case.get("passed")]
    ambiguous_cases = [case for case in cases if case.get("ambiguous")]
    return {
        "name": name,
        "profile_path": profile_path,
        "total": int(payload.get("total") or len(cases)),
        "passed": int(payload.get("passed") or 0),
        "failed": int(payload.get("failed") or len(failed_cases)),
        "pass_rate": float(payload.get("pass_rate") or 0.0),
        "failed_case_ids": [case.get("id") for case in failed_cases],
        "ambiguous_case_results": [
            {
                "id": case.get("id"),
                "passed": bool(case.get("passed")),
                "expected_top_qa_id": case.get("expected_top_qa_id"),
                "expected_any_qa_ids": list(case.get("expected_any_qa_ids") or []),
                "actual_top_qa_id": case.get("actual_top_qa_id"),
            }
            for case in ambiguous_cases
        ],
        "average_hybrid_score": _avg([top.get("hybrid_score") for top in top_candidates]),
        "average_semantic_score": _avg([top.get("semantic_score") for top in top_candidates]),
        "average_keyword_score": _avg([top.get("keyword_score") for top in top_candidates]),
        "average_weighted_keyword_score": _avg(
            [top.get("weighted_keyword_score") for top in top_candidates]
        ),
        "average_top1_top2_margin": _avg([top.get("top1_top2_margin") for top in top_candidates]),
        "failure_details": [_failure_detail(case) for case in failed_cases[:20]],
    }


def _set_profile_env(profile: str) -> str | None:
    if profile == "no_profile":
        os.environ.pop("APPROVED_SIMILAR_KEYWORD_WEIGHTS", None)
        return None
    os.environ["APPROVED_SIMILAR_KEYWORD_WEIGHTS"] = profile
    return profile


def _clear_profile_cache() -> None:
    if hasattr(approved_similar, "_load_keyword_weight_profile"):
        approved_similar._load_keyword_weight_profile.cache_clear()


def compare_profiles(
    *,
    cases: str | Path,
    collection: str | None,
    profiles: Sequence[str],
    output_json: str | Path | None = None,
    output_md: str | Path | None = None,
    top_k: int = 5,
    eval_fn: EvalFn = approved_similar_candidate_runner.run_eval,
) -> Dict[str, Any]:
    if not profiles:
        raise ValueError("at least one profile is required")

    previous_env = os.environ.get("APPROVED_SIMILAR_KEYWORD_WEIGHTS")
    previous_present = "APPROVED_SIMILAR_KEYWORD_WEIGHTS" in os.environ
    summaries: List[Dict[str, Any]] = []
    try:
        for profile in profiles:
            profile_path = _set_profile_env(profile)
            _clear_profile_cache()
            payload = eval_fn(
                cases=cases,
                output=None,
                collection=collection,
                top_k=top_k,
            )
            summaries.append(
                _summarize_profile(
                    name=_profile_name(profile),
                    profile_path=profile_path,
                    payload=payload,
                )
            )
    finally:
        if previous_present:
            os.environ["APPROVED_SIMILAR_KEYWORD_WEIGHTS"] = str(previous_env)
        else:
            os.environ.pop("APPROVED_SIMILAR_KEYWORD_WEIGHTS", None)
        _clear_profile_cache()

    report = {
        "cases": str(cases),
        "collection": collection,
        "top_k": top_k,
        "profiles": summaries,
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
        "# approved_similar_candidate Profile Comparison",
        "",
        f"- cases: `{report.get('cases')}`",
        f"- collection: `{report.get('collection')}`",
        f"- top_k: `{report.get('top_k')}`",
        "",
        "| profile | passed | failed | pass_rate | avg_hybrid | avg_keyword | avg_weighted_keyword | avg_margin |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for profile in report.get("profiles") or []:
        lines.append(
            "| {name} | {passed} | {failed} | {pass_rate:.3f} | {hybrid} | {keyword} | {weighted} | {margin} |".format(
                name=profile.get("name"),
                passed=profile.get("passed"),
                failed=profile.get("failed"),
                pass_rate=float(profile.get("pass_rate") or 0.0),
                hybrid=_fmt(profile.get("average_hybrid_score")),
                keyword=_fmt(profile.get("average_keyword_score")),
                weighted=_fmt(profile.get("average_weighted_keyword_score")),
                margin=_fmt(profile.get("average_top1_top2_margin")),
            )
        )
    lines.append("")
    for profile in report.get("profiles") or []:
        lines.append(f"## {profile.get('name')}")
        failed_ids = ", ".join(str(item) for item in profile.get("failed_case_ids") or [])
        lines.append(f"- failed_case_ids: {failed_ids or '(none)'}")
        ambiguous = profile.get("ambiguous_case_results") or []
        if ambiguous:
            lines.append("- ambiguous_case_results:")
            for case in ambiguous:
                lines.append(
                    f"  - {case.get('id')}: passed={case.get('passed')} actual={case.get('actual_top_qa_id')}"
                )
        failures = profile.get("failure_details") or []
        if failures:
            lines.append("- failure_details:")
            for detail in failures:
                lines.append(
                    f"  - {detail.get('id')}: actual={detail.get('actual_top_qa_id')} expected={detail.get('expected_top_qa_id')} query={detail.get('query')}"
                )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    return f"{float(value):.3f}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare approved_similar_candidate keyword profiles.")
    parser.add_argument("--cases", required=True)
    parser.add_argument("--collection", default=None)
    parser.add_argument("--profiles", nargs="+", required=True, help="Profile paths or literal no_profile.")
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args(argv)

    compare_profiles(
        cases=args.cases,
        collection=args.collection,
        profiles=args.profiles,
        output_json=args.output_json,
        output_md=args.output_md,
        top_k=args.top_k,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
