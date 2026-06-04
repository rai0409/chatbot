from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Sequence

from eval import approved_similar_candidate_runner
from rag_core import approved_similar
from rag_core.approved_similar import decide_approved_similar_candidate


_BLOCKED_OR_REVIEW_ROUTES = {
    "numeric_conflict_blocked",
    "negation_conflict_review",
    "ambiguous_multi_topic",
}


def _inc(mapping: Dict[str, int], key: str) -> None:
    mapping[key] = int(mapping.get(key, 0)) + 1


def _clear_profile_cache() -> None:
    if hasattr(approved_similar, "_load_keyword_weight_profile"):
        approved_similar._load_keyword_weight_profile.cache_clear()


def _case_decision_record(case: Dict[str, Any], *, collection: str | None, top_k: int, search_fn) -> Dict[str, Any]:
    result = approved_similar_candidate_runner.evaluate_case(
        case,
        collection=collection,
        top_k=top_k,
        search_fn=search_fn,
    )
    candidates = [dict(candidate or {}) for candidate in result.get("candidates") or []]
    if candidates:
        candidates[0]["ambiguous"] = bool(result.get("ambiguous", False))
    decision = decide_approved_similar_candidate(candidates)
    top = result.get("top_candidate") or {}
    return {
        "id": result.get("id"),
        "category": result.get("category"),
        "query": result.get("query"),
        "expected_top_qa_id": result.get("expected_top_qa_id"),
        "expected_any_qa_ids": list(result.get("expected_any_qa_ids") or []),
        "actual_top_qa_id": result.get("actual_top_qa_id"),
        "passed": bool(result.get("passed")),
        "decision": decision,
        "numeric_conflict": bool(top.get("numeric_conflict", False)),
        "negation_conflict": bool(top.get("negation_conflict", False)),
        "top_candidate_summary": decision.get("top_candidate_summary"),
    }


def _summarize(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(records)
    passed = sum(1 for record in records if record.get("passed"))
    failed = total - passed
    route_counts: Dict[str, int] = {}
    route_counts_by_passed_failed = {"passed": {}, "failed": {}}
    for record in records:
        route = str((record.get("decision") or {}).get("route") or "unknown")
        _inc(route_counts, route)
        bucket = "passed" if record.get("passed") else "failed"
        _inc(route_counts_by_passed_failed[bucket], route)

    failed_high_confidence = [
        _highlight_record(record)
        for record in records
        if not record.get("passed")
        and (record.get("decision") or {}).get("route") == "high_confidence_answer"
    ]
    passed_blocked_or_reviewed = [
        _highlight_record(record)
        for record in records
        if record.get("passed")
        and (record.get("decision") or {}).get("route") in _BLOCKED_OR_REVIEW_ROUTES
    ]
    numeric_not_blocked = [
        _highlight_record(record)
        for record in records
        if record.get("numeric_conflict")
        and (record.get("decision") or {}).get("route") != "numeric_conflict_blocked"
    ]
    negation_not_reviewed = [
        _highlight_record(record)
        for record in records
        if record.get("negation_conflict")
        and (record.get("decision") or {}).get("route") != "negation_conflict_review"
    ]

    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": (passed / total) if total else 0.0,
        "route_counts": route_counts,
        "high_confidence_count": route_counts.get("high_confidence_answer", 0),
        "blocked_or_review_count": sum(route_counts.get(route, 0) for route in _BLOCKED_OR_REVIEW_ROUTES),
        "candidate_only_count": route_counts.get("candidate_only", 0),
        "low_confidence_count": route_counts.get("low_confidence_no_answer", 0),
        "route_counts_by_passed_failed": route_counts_by_passed_failed,
        "failed_high_confidence_cases": failed_high_confidence,
        "passed_blocked_or_reviewed_cases": passed_blocked_or_reviewed,
        "numeric_conflict_not_blocked_cases": numeric_not_blocked,
        "negation_conflict_not_reviewed_cases": negation_not_reviewed,
    }


def _highlight_record(record: Dict[str, Any]) -> Dict[str, Any]:
    decision = record.get("decision") or {}
    return {
        "id": record.get("id"),
        "category": record.get("category"),
        "query": record.get("query"),
        "expected_top_qa_id": record.get("expected_top_qa_id"),
        "expected_any_qa_ids": list(record.get("expected_any_qa_ids") or []),
        "actual_top_qa_id": record.get("actual_top_qa_id"),
        "passed": bool(record.get("passed")),
        "route": decision.get("route"),
        "reasons": list(decision.get("reasons") or []),
        "score_snapshot": dict(decision.get("score_snapshot") or {}),
        "top_candidate_summary": record.get("top_candidate_summary"),
    }


def run_decision_eval(
    *,
    cases: str | Path,
    collection: str | None = None,
    profile: str | None = None,
    top_k: int = 5,
    output_json: str | Path | None = None,
    output_md: str | Path | None = None,
    search_fn=approved_similar_candidate_runner.search_approved_similar_candidates,
) -> Dict[str, Any]:
    previous_env = os.environ.get("APPROVED_SIMILAR_KEYWORD_WEIGHTS")
    previous_present = "APPROVED_SIMILAR_KEYWORD_WEIGHTS" in os.environ
    try:
        if profile:
            os.environ["APPROVED_SIMILAR_KEYWORD_WEIGHTS"] = str(profile)
        else:
            os.environ.pop("APPROVED_SIMILAR_KEYWORD_WEIGHTS", None)
        _clear_profile_cache()

        records = [
            _case_decision_record(case, collection=collection, top_k=top_k, search_fn=search_fn)
            for case in approved_similar_candidate_runner.load_cases(cases)
        ]
        report = {
            "cases": str(cases),
            "collection": collection,
            "profile": profile,
            "top_k": top_k,
            "summary": _summarize(records),
            "per_case": records,
        }
    finally:
        if previous_present:
            os.environ["APPROVED_SIMILAR_KEYWORD_WEIGHTS"] = str(previous_env)
        else:
            os.environ.pop("APPROVED_SIMILAR_KEYWORD_WEIGHTS", None)
        _clear_profile_cache()

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
    summary = report.get("summary") or {}
    lines = [
        "# approved_similar_candidate Decision Eval",
        "",
        f"- cases: `{report.get('cases')}`",
        f"- collection: `{report.get('collection')}`",
        f"- profile: `{report.get('profile')}`",
        f"- top_k: `{report.get('top_k')}`",
        "",
        "## Summary",
        "",
        f"- total: {summary.get('total')}",
        f"- passed: {summary.get('passed')}",
        f"- failed: {summary.get('failed')}",
        f"- pass_rate: {float(summary.get('pass_rate') or 0.0):.3f}",
        f"- high_confidence_count: {summary.get('high_confidence_count')}",
        f"- blocked_or_review_count: {summary.get('blocked_or_review_count')}",
        f"- candidate_only_count: {summary.get('candidate_only_count')}",
        f"- low_confidence_count: {summary.get('low_confidence_count')}",
        "",
        "## Route Counts",
        "",
    ]
    for route, count in sorted((summary.get("route_counts") or {}).items()):
        lines.append(f"- {route}: {count}")

    sections = [
        ("Failed High Confidence", "failed_high_confidence_cases"),
        ("Passed Blocked Or Reviewed", "passed_blocked_or_reviewed_cases"),
        ("Numeric Conflict Not Blocked", "numeric_conflict_not_blocked_cases"),
        ("Negation Conflict Not Reviewed", "negation_conflict_not_reviewed_cases"),
    ]
    for title, key in sections:
        lines.extend(["", f"## {title}", ""])
        items = summary.get(key) or []
        if not items:
            lines.append("(none)")
            continue
        for item in items:
            lines.append(
                f"- {item.get('id')}: route={item.get('route')} passed={item.get('passed')} actual={item.get('actual_top_qa_id')} query={item.get('query')}"
            )
    return "\n".join(lines).rstrip() + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate approved_similar_candidate decision gate routes.")
    parser.add_argument("--cases", required=True)
    parser.add_argument("--collection", default=None)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    args = parser.parse_args(argv)

    run_decision_eval(
        cases=args.cases,
        collection=args.collection,
        profile=args.profile,
        top_k=args.top_k,
        output_json=args.output_json,
        output_md=args.output_md,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
