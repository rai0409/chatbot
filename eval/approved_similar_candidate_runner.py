from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence

from rag_core.approved_similar import search_approved_similar_candidates


SearchFn = Callable[..., List[Dict[str, Any]]]

_MAX_TERMS = 8
_MAX_FIELDS = 8
_MAX_SYNONYM_MATCHES = 5
_MAX_FAILURE_REASONS = 6


def load_cases(path: str | Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            raw = line.strip()
            if not raw:
                continue
            record = json.loads(raw)
            if not record.get("query"):
                raise ValueError(f"case line {line_no} is missing query")
            if not record.get("expected_top_qa_id") and not record.get("expected_any_qa_ids"):
                raise ValueError(
                    f"case line {line_no} is missing expected_top_qa_id or expected_any_qa_ids"
                )
            records.append(record)
    return records


def _answer_preview(candidate: Dict[str, Any] | None) -> str | None:
    if candidate is None:
        return None
    for key in (
        "approved_answer_preview",
        "answer_text_preview",
        "approved_answer",
        "answer_text",
    ):
        value = candidate.get(key)
        if value is not None and str(value).strip() != "":
            return str(value)
    return None


def _bounded_candidate(candidate: Dict[str, Any] | None) -> Dict[str, Any] | None:
    if candidate is None:
        return None
    return {
        "qa_id": candidate.get("qa_id"),
        "question_text": candidate.get("question_text"),
        "answer_preview": _answer_preview(candidate),
        "semantic_score": candidate.get("semantic_score"),
        "semantic_distance": candidate.get("semantic_distance"),
        "keyword_score": candidate.get("keyword_score"),
        "hybrid_score": candidate.get("hybrid_score"),
        "top1_top2_margin": candidate.get("top1_top2_margin"),
        "margin_score_basis": candidate.get("margin_score_basis"),
        "numeric_conflict": bool(candidate.get("numeric_conflict", False)),
        "negation_conflict": bool(candidate.get("negation_conflict", False)),
        "matched_terms": list(candidate.get("matched_terms") or [])[:_MAX_TERMS],
        "matched_fields": list(candidate.get("matched_fields") or [])[:_MAX_FIELDS],
        "synonym_matches": list(candidate.get("synonym_matches") or [])[:_MAX_SYNONYM_MATCHES],
    }


def _failure_reasons(case: Dict[str, Any], top_candidate: Dict[str, Any] | None) -> List[str]:
    failures: List[str] = []
    expected_top = str(case.get("expected_top_qa_id") or "").strip()
    expected_any = [str(item) for item in case.get("expected_any_qa_ids") or [] if str(item).strip()]
    actual_top = str((top_candidate or {}).get("qa_id") or "")
    acceptable = set(expected_any)
    if expected_top:
        acceptable.add(expected_top)
    if actual_top not in acceptable:
        expected_label = expected_top
        if expected_any:
            expected_label = f"{expected_top or '<none>'} or one of {expected_any}"
        failures.append(f"expected top qa_id {expected_label}, got {actual_top or '<none>'}")

    if "expected_numeric_conflict" in case:
        expected = bool(case["expected_numeric_conflict"])
        actual = bool((top_candidate or {}).get("numeric_conflict", False))
        if actual != expected:
            failures.append(f"expected top numeric_conflict {expected}, got {actual}")

    if "expected_negation_conflict" in case:
        expected = bool(case["expected_negation_conflict"])
        actual = bool((top_candidate or {}).get("negation_conflict", False))
        if actual != expected:
            failures.append(f"expected top negation_conflict {expected}, got {actual}")

    if case.get("expected_synonym_evidence"):
        synonym_matches = list((top_candidate or {}).get("synonym_matches") or [])
        if not synonym_matches:
            failures.append("expected top synonym evidence, got none")

    return failures[:_MAX_FAILURE_REASONS]


def evaluate_case(
    case: Dict[str, Any],
    *,
    collection: str | None,
    top_k: int,
    search_fn: SearchFn = search_approved_similar_candidates,
) -> Dict[str, Any]:
    candidates = search_fn(
        str(case["query"]),
        collection_name=collection,
        top_k=top_k,
    )
    top_candidate = candidates[0] if candidates else None
    failures = _failure_reasons(case, top_candidate)
    return {
        "id": case.get("id") or case.get("case_id"),
        "category": case.get("category"),
        "source_question_no": case.get("source_question_no"),
        "ambiguous": bool(case.get("ambiguous", False)),
        "query": case["query"],
        "expected_top_qa_id": case.get("expected_top_qa_id"),
        "expected_any_qa_ids": list(case.get("expected_any_qa_ids") or []),
        "actual_top_qa_id": (top_candidate or {}).get("qa_id"),
        "top_answer_preview": _answer_preview(top_candidate),
        "passed": not failures,
        "failure_reasons": failures,
        "expected_numeric_conflict": case.get("expected_numeric_conflict"),
        "expected_negation_conflict": case.get("expected_negation_conflict"),
        "top_candidate": _bounded_candidate(top_candidate),
        "candidates": [_bounded_candidate(candidate) for candidate in candidates[:top_k]],
    }


def run_eval(
    *,
    cases: str | Path,
    output: str | Path | None = None,
    collection: str | None = None,
    top_k: int = 5,
    search_fn: SearchFn = search_approved_similar_candidates,
) -> Dict[str, Any]:
    if top_k < 1:
        raise ValueError("top_k must be >= 1")

    records = load_cases(cases)
    per_case = [
        evaluate_case(record, collection=collection, top_k=top_k, search_fn=search_fn)
        for record in records
    ]
    passed = sum(1 for result in per_case if result["passed"])
    failed = len(per_case) - passed
    payload = {
        "total": len(per_case),
        "passed": passed,
        "failed": failed,
        "pass_rate": (passed / len(per_case)) if per_case else 0.0,
        "collection": collection,
        "top_k": top_k,
        "per_case": per_case,
    }

    if output is not None:
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def _default_cases_path() -> Path:
    return Path(__file__).with_name("approved_similar_candidate_cases.jsonl")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate debug approved_similar_candidate retrieval.")
    parser.add_argument("--cases", default=str(_default_cases_path()))
    parser.add_argument("--output", required=True)
    parser.add_argument("--collection", default=None)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args(argv)

    payload = run_eval(
        cases=args.cases,
        output=args.output,
        collection=args.collection,
        top_k=args.top_k,
    )
    print(
        f"Summary: passed={payload['passed']}/{payload['total']} "
        f"failed={payload['failed']} pass_rate={payload['pass_rate']:.3f}"
    )
    return 0 if payload["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
