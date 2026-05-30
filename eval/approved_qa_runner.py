from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from rag_core.approved_qa import (
    load_approved_qa,
    lookup_approved_answer,
    validate_approved_qa_records,
)
from rag_core.question_normalization import normalize_question_for_exact_match


def _read_jsonl(path: Path) -> List[dict]:
    records: List[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if raw:
                records.append(json.loads(raw))
    return records


def run_approved_qa_eval(cases: str | Path, output: str | Path | None = None) -> Dict[str, Any]:
    cases_path = Path(cases)
    records = _read_jsonl(cases_path)
    validation_errors = validate_approved_qa_records(records)
    index = load_approved_qa(cases_path)

    results: List[Dict[str, Any]] = []
    for record in records:
        if str(record.get("status") or "").strip() != "approved":
            continue
        qa_id = str(record.get("qa_id") or "")
        question = str(record.get("question") or "")
        expected_answer = str(record.get("approved_answer") or "")
        expected_citations = record.get("approved_citations") or []
        normalized = normalize_question_for_exact_match(record.get("normalized_question") or question)
        matched = lookup_approved_answer(index, question)

        failures: List[str] = []
        if validation_errors:
            failures.extend(validation_errors)
        if matched is None:
            failures.append("missing exact match")
        else:
            if matched.qa_id != qa_id:
                failures.append(f"qa_id mismatch: {matched.qa_id}")
            if matched.approved_answer != expected_answer:
                failures.append("approved_answer mismatch")
            if len(matched.approved_citations) != len(expected_citations):
                failures.append("approved_citations length mismatch")

        passed = not failures
        results.append(
            {
                "qa_id": qa_id,
                "passed": passed,
                "failures": failures,
                "question": question,
                "normalized_question": normalized,
                "matched_qa_id": matched.qa_id if matched is not None else None,
                "answer_exact_match": bool(matched and matched.approved_answer == expected_answer),
            }
        )

    passed_count = sum(1 for item in results if item["passed"])
    failed_count = len(results) - passed_count
    payload = {
        "summary": {
            "passed": passed_count,
            "failed": failed_count,
            "total": len(results),
            "pass_rate": (passed_count / len(results)) if results else 0.0,
        },
        "results": results,
    }
    if output is not None:
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate approved QA exact-match records.")
    parser.add_argument("--cases", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    payload = run_approved_qa_eval(args.cases, args.output)
    for result in payload["results"]:
        if result["passed"]:
            print(f"[PASS] {result['qa_id']}")
        else:
            print(f"[FAIL] {result['qa_id']} {'; '.join(result['failures'])}")
    summary = payload["summary"]
    print(
        f"Summary: passed={summary['passed']}/{summary['total']} "
        f"failed={summary['failed']} pass_rate={summary['pass_rate']:.3f}"
    )
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
