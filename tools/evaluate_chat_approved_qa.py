#!/usr/bin/env python3
"""Evaluate fixed QA cases against the live /chat approved exact path."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
from pathlib import Path
from typing import Any


DEFAULT_CASES = Path("artifacts/fixed_qa_eval/fixed_qa_cases.jsonl")
DEFAULT_OUTPUT_DIR = Path("artifacts/current_qa_hybrid_analysis/chat_exact_qa_eval")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def post_chat(chat_url: str, question: str, timeout: int) -> tuple[dict[str, Any] | None, str]:
    cmd = [
        "curl",
        "-sS",
        "-X",
        "POST",
        chat_url,
        "-H",
        "Content-Type: application/json",
        "-d",
        json.dumps({"question": question, "top_k": 5}, ensure_ascii=False),
    ]
    try:
        raw = subprocess.check_output(cmd, text=True, timeout=timeout)
    except subprocess.CalledProcessError as exc:
        return None, f"curl_exit_{exc.returncode}: {exc.output or exc}"
    except subprocess.TimeoutExpired:
        return None, "timeout"
    try:
        return json.loads(raw), ""
    except json.JSONDecodeError as exc:
        return None, f"invalid_json: {exc}: {raw[:300]}"


def expected_source_doc(case: dict[str, Any]) -> str:
    source_doc = str(case.get("source_doc") or "").strip()
    if source_doc:
        return Path(source_doc).name
    source_pdf = str(case.get("source_pdf") or "").strip()
    return Path(source_pdf).name if source_pdf else ""


def citation_source_docs(payload: dict[str, Any]) -> list[str]:
    docs = []
    for citation in payload.get("citations") or []:
        if not isinstance(citation, dict):
            continue
        source_doc = str(citation.get("source_doc") or "").strip()
        if source_doc:
            docs.append(Path(source_doc).name)
    return sorted(set(docs))


def evaluate_case(case: dict[str, Any], payload: dict[str, Any] | None, error: str) -> dict[str, Any]:
    expected = str(case.get("expected_answer") or case.get("approved_answer") or "").strip()
    answer = str((payload or {}).get("answer_text") or (payload or {}).get("answer") or "").strip()
    expected_doc = expected_source_doc(case)
    actual_docs = citation_source_docs(payload or {})
    answer_match = bool(expected and (answer == expected or expected in answer))
    source_doc_match = bool(expected_doc and expected_doc in actual_docs)
    approved_exact = (
        (payload or {}).get("answer_mode") == "approved_exact_match"
        and (payload or {}).get("retrieval_source") == "approved_qa_exact"
    )
    return {
        "case_id": case.get("case_id", ""),
        "question": case.get("question", ""),
        "error": error,
        "answer_text": answer,
        "expected_answer": expected,
        "answer_match": answer_match,
        "expected_source_doc": expected_doc,
        "actual_source_docs": "|".join(actual_docs),
        "source_doc_match": source_doc_match,
        "answer_mode": (payload or {}).get("answer_mode", ""),
        "retrieval_source": (payload or {}).get("retrieval_source", ""),
        "approved_qa_id": (payload or {}).get("approved_qa_id", ""),
        "approved_exact": approved_exact,
        "used_fallback": (payload or {}).get("used_fallback", ""),
        "guard_reason": (payload or {}).get("guard_reason", ""),
        "citations_count": len((payload or {}).get("citations") or []),
    }


def write_outputs(results: list[dict[str, Any]], output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / "chat_exact_qa_eval_results.jsonl"
    csv_path = output_dir / "chat_exact_qa_eval_results.csv"
    report_path = output_dir / "chat_exact_qa_eval_report.md"

    with jsonl_path.open("w", encoding="utf-8") as f:
        for result in results:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        fieldnames = list(results[0].keys()) if results else ["case_id"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    total = len(results)
    errors = sum(1 for row in results if row["error"])
    answer_matches = sum(1 for row in results if row["answer_match"])
    source_matches = sum(1 for row in results if row["source_doc_match"])
    approved_exact = sum(1 for row in results if row["approved_exact"])
    failures = [
        row["case_id"]
        for row in results
        if row["error"] or not row["answer_match"] or not row["source_doc_match"] or not row["approved_exact"]
    ]
    summary = {
        "total_cases": total,
        "errors": errors,
        "answer_match_rate": answer_matches / total if total else 0.0,
        "source_doc_match_rate": source_matches / total if total else 0.0,
        "approved_exact_rate": approved_exact / total if total else 0.0,
        "llm_fallback_count": sum(1 for row in results if not row["approved_exact"]),
        "failed_cases": failures,
        "jsonl": str(jsonl_path),
        "csv": str(csv_path),
        "report": str(report_path),
    }

    lines = [
        "# Chat Exact QA Evaluation Report",
        "",
        f"- total_cases: {summary['total_cases']}",
        f"- errors: {summary['errors']}",
        f"- answer_match_rate: {summary['answer_match_rate']:.3f}",
        f"- source_doc_match_rate: {summary['source_doc_match_rate']:.3f}",
        f"- approved_exact_rate: {summary['approved_exact_rate']:.3f}",
        f"- llm_fallback_count: {summary['llm_fallback_count']}",
        "",
        "## Failed Cases",
        "",
    ]
    if failures:
        lines.extend(f"- {case_id}" for case_id in failures[:100])
    else:
        lines.append("- none")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate live /chat approved exact QA responses.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--chat-url", default="http://127.0.0.1:8000/chat")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--timeout", type=int, default=10)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cases = read_jsonl(args.cases)
    if args.limit is not None:
        cases = cases[: args.limit]
    results = []
    for case in cases:
        payload, error = post_chat(args.chat_url, str(case.get("question") or ""), args.timeout)
        results.append(evaluate_case(case, payload, error))
    summary = write_outputs(results, args.output_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not summary["failed_cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
