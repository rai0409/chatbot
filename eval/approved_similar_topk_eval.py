from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Sequence

from eval import approved_similar_candidate_runner
from rag_core import approved_similar


def _clear_profile_cache() -> None:
    if hasattr(approved_similar, "_load_keyword_weight_profile"):
        approved_similar._load_keyword_weight_profile.cache_clear()


def _expected_ids(case: Dict[str, Any]) -> List[str]:
    ids = [str(item) for item in case.get("expected_any_qa_ids") or [] if str(item).strip()]
    expected_top = str(case.get("expected_top_qa_id") or "").strip()
    if expected_top and expected_top not in ids:
        ids.insert(0, expected_top)
    return ids


def _score_summary(candidate: Dict[str, Any] | None) -> Dict[str, Any] | None:
    if candidate is None:
        return None
    return {
        "hybrid_score": candidate.get("hybrid_score"),
        "semantic_score": candidate.get("semantic_score"),
        "keyword_score": candidate.get("keyword_score"),
        "weighted_keyword_score": candidate.get("weighted_keyword_score"),
        "top1_top2_margin": candidate.get("top1_top2_margin"),
    }


def _candidate_summary(candidate: Dict[str, Any] | None) -> Dict[str, Any] | None:
    if candidate is None:
        return None
    return {
        "qa_id": candidate.get("qa_id"),
        "question_text": candidate.get("question_text"),
        "answer_preview": candidate.get("answer_preview"),
        "generic_matched_terms": list(candidate.get("generic_matched_terms") or []),
        "specific_matched_terms": list(candidate.get("specific_matched_terms") or []),
        "matched_terms": list(candidate.get("matched_terms") or []),
    }


def evaluate_topk_case(case: Dict[str, Any], *, collection: str | None, top_k: int, search_fn) -> Dict[str, Any]:
    result = approved_similar_candidate_runner.evaluate_case(
        case,
        collection=collection,
        top_k=top_k,
        search_fn=search_fn,
    )
    candidates = list(result.get("candidates") or [])
    candidate_qa_ids = [candidate.get("qa_id") for candidate in candidates]
    expected = set(_expected_ids(case))
    correct_rank = None
    for index, qa_id in enumerate(candidate_qa_ids, start=1):
        if str(qa_id) in expected:
            correct_rank = index
            break
    return {
        "id": result.get("id"),
        "category": result.get("category"),
        "source_question_no": result.get("source_question_no"),
        "ambiguous": bool(result.get("ambiguous", False)),
        "query": result.get("query"),
        "expected_top_qa_id": result.get("expected_top_qa_id"),
        "expected_any_qa_ids": list(result.get("expected_any_qa_ids") or []),
        "actual_top_qa_id": result.get("actual_top_qa_id"),
        "candidate_qa_ids": candidate_qa_ids,
        "correct_rank": correct_rank,
        "found_in_top1": correct_rank == 1,
        "found_in_top3": correct_rank is not None and correct_rank <= min(3, top_k),
        "found_in_top5": correct_rank is not None and correct_rank <= min(5, top_k),
        "missed_top_k": correct_rank is None,
        "top_candidate_scores": _score_summary(candidates[0] if candidates else None),
        "top_candidate_summary": _candidate_summary(candidates[0] if candidates else None),
    }


def _summarize(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(records)
    top1_hits = sum(1 for record in records if record.get("found_in_top1"))
    top3_hits = sum(1 for record in records if record.get("found_in_top3"))
    top5_hits = sum(1 for record in records if record.get("found_in_top5"))
    reciprocal_ranks = [
        1.0 / int(record["correct_rank"])
        for record in records
        if record.get("correct_rank") is not None
    ]
    failed_top1_found_top3 = [
        record.get("id")
        for record in records
        if not record.get("found_in_top1") and record.get("found_in_top3")
    ]
    failed_top1_found_top5 = [
        record.get("id")
        for record in records
        if not record.get("found_in_top1") and record.get("found_in_top5")
    ]
    ambiguous = [record for record in records if record.get("ambiguous")]
    return {
        "total": total,
        "top1_hits": top1_hits,
        "top3_hits": top3_hits,
        "top5_hits": top5_hits,
        "top1_accuracy": (top1_hits / total) if total else 0.0,
        "top3_recall": (top3_hits / total) if total else 0.0,
        "top5_recall": (top5_hits / total) if total else 0.0,
        "mrr": (sum(reciprocal_ranks) / total) if total else 0.0,
        "failed_top1_but_found_in_top3": failed_top1_found_top3,
        "failed_top1_but_found_in_top5": failed_top1_found_top5,
        "missed_all_top_k_case_ids": [record.get("id") for record in records if record.get("missed_top_k")],
        "ambiguous_case_summary": {
            "total": len(ambiguous),
            "top1_hits": sum(1 for record in ambiguous if record.get("found_in_top1")),
            "top3_hits": sum(1 for record in ambiguous if record.get("found_in_top3")),
            "top5_hits": sum(1 for record in ambiguous if record.get("found_in_top5")),
            "missed_top_k": sum(1 for record in ambiguous if record.get("missed_top_k")),
        },
    }


def run_topk_eval(
    *,
    cases: str | Path,
    collection: str | None = None,
    profile: str | None = None,
    top_k: int = 5,
    output_json: str | Path | None = None,
    output_md: str | Path | None = None,
    search_fn=approved_similar_candidate_runner.search_approved_similar_candidates,
) -> Dict[str, Any]:
    if top_k < 1:
        raise ValueError("top_k must be >= 1")

    previous_env = os.environ.get("APPROVED_SIMILAR_KEYWORD_WEIGHTS")
    previous_present = "APPROVED_SIMILAR_KEYWORD_WEIGHTS" in os.environ
    try:
        if profile:
            os.environ["APPROVED_SIMILAR_KEYWORD_WEIGHTS"] = str(profile)
        else:
            os.environ.pop("APPROVED_SIMILAR_KEYWORD_WEIGHTS", None)
        _clear_profile_cache()
        records = [
            evaluate_topk_case(case, collection=collection, top_k=top_k, search_fn=search_fn)
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
        "# approved_similar_candidate Top-K Eval",
        "",
        f"- cases: `{report.get('cases')}`",
        f"- collection: `{report.get('collection')}`",
        f"- profile: `{report.get('profile')}`",
        f"- top_k: `{report.get('top_k')}`",
        "",
        "## Summary",
        "",
        "| metric | value |",
        "|---|---:|",
        f"| total | {summary.get('total')} |",
        f"| top1_hits | {summary.get('top1_hits')} |",
        f"| top3_hits | {summary.get('top3_hits')} |",
        f"| top5_hits | {summary.get('top5_hits')} |",
        f"| top1_accuracy | {float(summary.get('top1_accuracy') or 0.0):.3f} |",
        f"| top3_recall | {float(summary.get('top3_recall') or 0.0):.3f} |",
        f"| top5_recall | {float(summary.get('top5_recall') or 0.0):.3f} |",
        f"| mrr | {float(summary.get('mrr') or 0.0):.3f} |",
        "",
        "## Missed Top1 But Found In Top3",
        "",
        _format_id_list(summary.get("failed_top1_but_found_in_top3") or []),
        "",
        "## Missed Top1 But Found In Top5",
        "",
        _format_id_list(summary.get("failed_top1_but_found_in_top5") or []),
        "",
        "## Missed Entire Top-K",
        "",
        _format_id_list(summary.get("missed_all_top_k_case_ids") or []),
        "",
        "## Per-Case Compact Details",
        "",
    ]
    for record in report.get("per_case") or []:
        lines.append(
            "- {id}: rank={rank} top={top} candidates={candidates}".format(
                id=record.get("id"),
                rank=record.get("correct_rank"),
                top=record.get("actual_top_qa_id"),
                candidates=",".join(str(item) for item in record.get("candidate_qa_ids") or []),
            )
        )
    return "\n".join(lines).rstrip() + "\n"


def _format_id_list(ids: Sequence[Any]) -> str:
    if not ids:
        return "(none)"
    return "\n".join(f"- {item}" for item in ids)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate approved_similar_candidate top-k recall and MRR.")
    parser.add_argument("--cases", required=True)
    parser.add_argument("--collection", default=None)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    args = parser.parse_args(argv)

    run_topk_eval(
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
