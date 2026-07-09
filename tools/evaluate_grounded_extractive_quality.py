#!/usr/bin/env python3
"""Evaluate grounded extractive /chat answer quality for in-corpus questions."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_CASES = Path("artifacts/grounded_extractive_quality/grounded_extractive_quality_cases.jsonl")
DEFAULT_OUTPUT_DIR = Path("artifacts/grounded_extractive_quality")
DEFAULT_CHAT_URL = "http://127.0.0.1:8010/chat"

ABSTAIN_MARKERS = (
    "関連情報が見つかりません",
    "根拠不足",
    "関連記載なし",
    "回答できません",
    "判断できません",
    "確認できません",
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{line_no}: invalid JSONL: {exc}") from exc
        obj.setdefault("case_id", f"case_{line_no:04d}")
        rows.append(obj)
    return rows


def post_chat(chat_url: str, question: str, timeout: int) -> tuple[int, dict[str, Any] | None, str]:
    payload = json.dumps({"question": question, "top_k": 5}, ensure_ascii=False)
    proc = subprocess.run(
        [
            "curl",
            "-sS",
            "-m",
            str(timeout),
            "-X",
            "POST",
            chat_url,
            "-H",
            "Content-Type: application/json",
            "-d",
            payload,
            "-w",
            "\n%{http_code}",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    raw = proc.stdout.strip()
    if proc.returncode != 0:
        return proc.returncode, None, proc.stderr.strip() or raw
    http_status = 0
    if "\n" in raw:
        body, status_text = raw.rsplit("\n", 1)
        try:
            http_status = int(status_text.strip())
            raw = body.strip()
        except ValueError:
            http_status = 0
    if http_status and http_status != 200:
        return http_status, None, f"http_status_{http_status}: {raw}"
    try:
        return http_status or 200, json.loads(raw), raw
    except json.JSONDecodeError:
        return 1, None, raw


def _answer_text(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""
    return str(payload.get("answer_text") or payload.get("answer") or "")


def _citations(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    raw = (payload or {}).get("citations") or []
    return [item for item in raw if isinstance(item, dict)]


def _retrieved(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    raw = (payload or {}).get("retrieved") or []
    return [item for item in raw if isinstance(item, dict)]


def _pages(value: Any) -> set[int]:
    if value in (None, "", []):
        return set()
    if isinstance(value, int):
        return {value}
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("[") and text.endswith("]"):
            try:
                return _pages(json.loads(text))
            except Exception:
                pass
        return {int(part.strip()) for part in text.replace("[", "").replace("]", "").split(",") if part.strip().isdigit()}
    if isinstance(value, list):
        out: set[int] = set()
        for item in value:
            out |= _pages(item)
        return out
    return set()


def _source_doc_match(items: list[dict[str, Any]], expected: str) -> bool:
    if not expected:
        return False
    for item in items:
        meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else item
        source_doc = str(meta.get("source_doc") or meta.get("doc_id") or item.get("source_doc") or "")
        if Path(source_doc).name == Path(expected).name:
            return True
    return False


def _page_match(items: list[dict[str, Any]], expected_pages: list[int]) -> bool:
    expected = {int(page) for page in expected_pages}
    if not expected:
        return False
    for item in items:
        meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else item
        pages = (
            _pages(meta.get("source_pages"))
            or _pages(item.get("source_pages"))
            or _pages(meta.get("source_page_start"))
            or _pages(meta.get("source_page_end"))
        )
        if pages & expected:
            return True
    return False


def evaluate_case(case: dict[str, Any], payload: dict[str, Any] | None, status: int, error: str) -> dict[str, Any]:
    answer = _answer_text(payload)
    citations = _citations(payload)
    retrieved = _retrieved(payload)
    evidence_items = citations + retrieved
    expected_mode = str(case.get("expected_answer_mode") or "grounded_extractive")
    expected_doc = str(case.get("expected_source_doc") or "")
    expected_pages = [int(page) for page in case.get("expected_source_pages") or []]
    terms = [str(term) for term in case.get("required_answer_terms") or []]
    answer_mode = str((payload or {}).get("answer_mode") or "")
    guard_reason = str((payload or {}).get("guard_reason") or "")
    used_fallback = bool((payload or {}).get("used_fallback"))
    abstained = bool(guard_reason or any(marker in answer for marker in ABSTAIN_MARKERS))

    checks = {
        "http_200": status == 200 and not error,
        "answer_nonempty": bool(answer.strip()),
        "answer_mode_match": answer_mode == expected_mode,
        "citations_present": bool(citations),
        "source_doc_match": _source_doc_match(evidence_items, expected_doc),
        "page_match": _page_match(evidence_items, expected_pages),
        "required_terms_present": all(term in answer for term in terms),
        "not_abstained": not abstained,
        "not_used_fallback": not used_fallback,
    }
    failed_checks = [name for name, ok in checks.items() if not ok]
    return {
        "case_id": case.get("case_id", ""),
        "category": case.get("category", ""),
        "question": case.get("question", ""),
        "http_status": status,
        "error": error,
        "answer_mode": answer_mode,
        "guard_reason": guard_reason,
        "used_fallback": used_fallback,
        "citations_count": len(citations),
        "retrieved_count": len(retrieved),
        "expected_source_doc": expected_doc,
        "expected_source_pages": "|".join(str(page) for page in expected_pages),
        "required_answer_terms": "|".join(terms),
        **checks,
        "failed_checks": failed_checks,
        "passed": not failed_checks,
        "answer_text": answer,
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "case_id",
        "category",
        "passed",
        "http_status",
        "error",
        "answer_mode",
        "guard_reason",
        "used_fallback",
        "citations_count",
        "retrieved_count",
        "expected_source_doc",
        "expected_source_pages",
        "required_answer_terms",
        "http_200",
        "answer_nonempty",
        "answer_mode_match",
        "citations_present",
        "source_doc_match",
        "page_match",
        "required_terms_present",
        "not_abstained",
        "not_used_fallback",
        "failed_checks",
        "question",
        "answer_text",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    return {
        "total_cases": total,
        "passed_cases": sum(1 for row in rows if row.get("passed") is True),
        "failed_cases": sum(1 for row in rows if row.get("passed") is not True),
        "pass_rate": sum(1 for row in rows if row.get("passed") is True) / total if total else 0.0,
        "http_errors": sum(1 for row in rows if row.get("http_200") is not True),
        "empty_answers": sum(1 for row in rows if row.get("answer_nonempty") is not True),
        "answer_mode_mismatches": sum(1 for row in rows if row.get("answer_mode_match") is not True),
        "missing_citations": sum(1 for row in rows if row.get("citations_present") is not True),
        "source_doc_misses": sum(1 for row in rows if row.get("source_doc_match") is not True),
        "page_misses": sum(1 for row in rows if row.get("page_match") is not True),
        "required_term_misses": sum(1 for row in rows if row.get("required_terms_present") is not True),
        "abstained_count": sum(1 for row in rows if row.get("not_abstained") is not True),
        "fallback_count": sum(1 for row in rows if row.get("not_used_fallback") is not True),
        "category_distribution": dict(sorted(Counter(str(row.get("category") or "") for row in rows).items())),
        "failed_case_ids": [row.get("case_id", "") for row in rows if row.get("passed") is not True],
    }


def write_report(path: Path, summary: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    lines = [
        "# Grounded Extractive Answer Quality Evaluation",
        "",
        "## Executive Summary",
        f"- total_cases: {summary['total_cases']}",
        f"- passed_cases: {summary['passed_cases']}",
        f"- failed_cases: {summary['failed_cases']}",
        f"- pass_rate: {summary['pass_rate']:.3f}",
        "",
        "## Gate Metrics",
        f"- http_errors: {summary['http_errors']}",
        f"- empty_answers: {summary['empty_answers']}",
        f"- answer_mode_mismatches: {summary['answer_mode_mismatches']}",
        f"- missing_citations: {summary['missing_citations']}",
        f"- source_doc_misses: {summary['source_doc_misses']}",
        f"- page_misses: {summary['page_misses']}",
        f"- required_term_misses: {summary['required_term_misses']}",
        f"- abstained_count: {summary['abstained_count']}",
        f"- fallback_count: {summary['fallback_count']}",
        "",
        "## Failed Cases",
    ]
    failures = [row for row in rows if row.get("passed") is not True]
    if failures:
        for row in failures:
            lines.append(
                f"- {row['case_id']}: failed_checks={row['failed_checks']}, "
                f"answer_mode={row['answer_mode']}, guard_reason={row['guard_reason']}, "
                f"source_doc={row['source_doc_match']}, page={row['page_match']}"
            )
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Commercial Judgment",
            "- This gate checks useful grounded extractive answers for in-corpus questions.",
            "- It does not validate LLM mode answer quality.",
            "- It does not validate DOCX/CSV/XLSX/PPTX support.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--chat-url", default=DEFAULT_CHAT_URL)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()

    cases = read_jsonl(args.cases)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for case in cases:
        status, payload, raw_error = post_chat(args.chat_url, str(case.get("question") or ""), args.timeout)
        error = raw_error if payload is None or status != 200 else ""
        rows.append(evaluate_case(case, payload, status, error))
    summary = summarize(rows)
    write_jsonl(args.output_dir / "grounded_extractive_quality_results.jsonl", rows)
    write_csv(args.output_dir / "grounded_extractive_quality_results.csv", rows)
    (args.output_dir / "grounded_extractive_quality_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_report(args.output_dir / "grounded_extractive_quality_report.md", summary, rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["failed_cases"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
