#!/usr/bin/env python3
"""Evaluate whether /chat abstains or stays grounded for unknown questions."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_CASES = Path("artifacts/unknown_abstention_eval/unknown_questions.jsonl")
DEFAULT_OUTPUT_DIR = Path("artifacts/unknown_abstention_eval")
DEFAULT_CHAT_URL = "http://127.0.0.1:8000/chat"

ABSTAIN_PHRASES = (
    "関連情報が見つかりません",
    "根拠",
    "判断できません",
    "確認できません",
    "分かりません",
    "わかりません",
    "不明",
    "不足",
    "資料には",
    "記載されていません",
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{line_no}: invalid JSONL: {exc}") from exc
        item.setdefault("case_id", f"case_{line_no:04d}")
        item.setdefault("category", "")
        item.setdefault("question", "")
        cases.append(item)
    return cases


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
    status_code = 0
    if "\n" in raw:
        body, status_text = raw.rsplit("\n", 1)
        try:
            status_code = int(status_text.strip())
            raw = body.strip()
        except ValueError:
            status_code = 0
    if status_code and status_code != 200:
        return status_code, None, f"http_status_{status_code}: {raw}"
    try:
        return 0, json.loads(raw), raw
    except json.JSONDecodeError:
        return 1, None, raw


def _answer_text(payload: dict[str, Any]) -> str:
    value = payload.get("answer_text")
    if value is None:
        value = payload.get("answer")
    return str(value or "")


def _has_abstain_signal(payload: dict[str, Any], answer_text: str) -> bool:
    if payload.get("abstained") is True:
        return True
    if payload.get("guard_reason"):
        return True
    return any(phrase in answer_text for phrase in ABSTAIN_PHRASES)


def classify_response(case: dict[str, Any], payload: dict[str, Any] | None, error: str) -> dict[str, Any]:
    question = str(case.get("question", ""))
    if payload is None:
        return {
            "case_id": case.get("case_id", ""),
            "category": case.get("category", ""),
            "question": question,
            "classification": "error",
            "error": error,
            "abstained": False,
            "grounded_answer": False,
            "unsupported_answer": False,
            "approved_exact_false_positive": False,
            "citations_count": 0,
            "guard_reason": "",
            "used_fallback": "",
            "answer_mode": "",
            "retrieval_source": "",
            "answer_text": "",
        }

    answer_text = _answer_text(payload)
    citations = payload.get("citations") or []
    if not isinstance(citations, list):
        citations = []
    citations_count = len(citations)
    guard_reason = str(payload.get("guard_reason") or "")
    used_fallback = bool(payload.get("used_fallback"))
    answer_mode = str(payload.get("answer_mode") or "")
    retrieval_source = str(payload.get("retrieval_source") or "")
    approved_exact_false_positive = (
        answer_mode in {"approved_exact_match", "approved_alias_match"}
        or retrieval_source in {"approved_qa_exact", "approved_qa_alias"}
    )
    abstained = _has_abstain_signal(payload, answer_text)
    grounded_answer = bool(citations_count > 0 and not abstained and not approved_exact_false_positive)
    unsupported_answer = bool(approved_exact_false_positive or (not abstained and citations_count == 0))

    if unsupported_answer:
        classification = "unsupported"
    elif abstained:
        classification = "abstained"
    elif grounded_answer:
        classification = "grounded"
    else:
        classification = "other"

    return {
        "case_id": case.get("case_id", ""),
        "category": case.get("category", ""),
        "question": question,
        "classification": classification,
        "error": "",
        "abstained": abstained,
        "grounded_answer": grounded_answer,
        "unsupported_answer": unsupported_answer,
        "approved_exact_false_positive": approved_exact_false_positive,
        "citations_count": citations_count,
        "guard_reason": guard_reason,
        "used_fallback": used_fallback,
        "answer_mode": answer_mode,
        "retrieval_source": retrieval_source,
        "answer_text": answer_text,
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "case_id",
        "category",
        "classification",
        "abstained",
        "grounded_answer",
        "unsupported_answer",
        "approved_exact_false_positive",
        "citations_count",
        "guard_reason",
        "used_fallback",
        "answer_mode",
        "retrieval_source",
        "error",
        "question",
        "answer_text",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    guard_reason_distribution = Counter(str(row.get("guard_reason") or "") for row in rows)
    used_fallback_distribution = Counter(str(row.get("used_fallback")) for row in rows)
    category_distribution = Counter(str(row.get("category") or "") for row in rows)
    classification_distribution = Counter(str(row.get("classification") or "") for row in rows)
    return {
        "total_cases": len(rows),
        "errors": sum(1 for row in rows if row.get("classification") == "error"),
        "abstained_count": sum(1 for row in rows if row.get("abstained") is True),
        "grounded_answer_count": sum(1 for row in rows if row.get("grounded_answer") is True),
        "unsupported_answer_count": sum(1 for row in rows if row.get("unsupported_answer") is True),
        "approved_exact_false_positive_count": sum(
            1 for row in rows if row.get("approved_exact_false_positive") is True
        ),
        "citations_count": sum(int(row.get("citations_count") or 0) for row in rows),
        "guard_reason_distribution": dict(sorted(guard_reason_distribution.items())),
        "used_fallback_distribution": dict(sorted(used_fallback_distribution.items())),
        "category_distribution": dict(sorted(category_distribution.items())),
        "classification_distribution": dict(sorted(classification_distribution.items())),
    }


def write_report(path: Path, summary: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    unsupported = [row for row in rows if row.get("unsupported_answer") is True]
    errors = [row for row in rows if row.get("classification") == "error"]
    status = "passed" if summary["unsupported_answer_count"] == 0 and summary["errors"] == 0 else "failed"
    lines = [
        "# Unknown Abstention Evaluation Report",
        "",
        "## Executive Summary",
        f"- status: {status}",
        f"- total_cases: {summary['total_cases']}",
        f"- errors: {summary['errors']}",
        f"- abstained_count: {summary['abstained_count']}",
        f"- grounded_answer_count: {summary['grounded_answer_count']}",
        f"- unsupported_answer_count: {summary['unsupported_answer_count']}",
        f"- approved_exact_false_positive_count: {summary['approved_exact_false_positive_count']}",
        "",
        "## Evaluation Result",
        f"- citations_count: {summary['citations_count']}",
        f"- classification_distribution: `{json.dumps(summary['classification_distribution'], ensure_ascii=False)}`",
        f"- used_fallback_distribution: `{json.dumps(summary['used_fallback_distribution'], ensure_ascii=False)}`",
        "",
        "## Guard Reason Distribution",
    ]
    for reason, count in summary["guard_reason_distribution"].items():
        label = reason if reason else "(empty)"
        lines.append(f"- {label}: {count}")
    lines.extend(["", "## Unsupported Answer Cases"])
    if unsupported:
        for row in unsupported:
            lines.append(
                f"- {row['case_id']} [{row['category']}]: "
                f"answer_mode={row['answer_mode']}, retrieval_source={row['retrieval_source']}, "
                f"citations={row['citations_count']}, question={row['question']}"
            )
    else:
        lines.append("- none")
    lines.extend(["", "## Error Cases"])
    if errors:
        for row in errors:
            lines.append(f"- {row['case_id']} [{row['category']}]: {row['error']}")
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Commercial Judgment",
            "- approved_qa_exact false positive is acceptable only at 0.",
            "- Unknown questions should abstain or return clearly grounded answers with citations.",
            f"- judgment: {'commercial abstention gate passed' if status == 'passed' else 'commercial abstention gate failed'}",
            "",
            "## Next Steps",
            "- Review every unsupported case and decide whether to improve guardrails, retrieval thresholds, or abstention policy.",
            "- Add these unknown questions to a recurring regression suite.",
            "- Keep exact QA, unknown abstention, and normal retrieval evaluations as separate gates.",
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

    rows: list[dict[str, Any]] = []
    for case in cases:
        returncode, payload, raw_error = post_chat(args.chat_url, str(case.get("question") or ""), args.timeout)
        error = raw_error if returncode else ""
        rows.append(classify_response(case, payload, error))

    summary = summarize(rows)
    write_jsonl(args.output_dir / "unknown_abstention_results.jsonl", rows)
    write_csv(args.output_dir / "unknown_abstention_results.csv", rows)
    write_report(args.output_dir / "unknown_abstention_report.md", summary, rows)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
