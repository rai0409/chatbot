#!/usr/bin/env python3
"""Generate reviewable QA candidates from a PDF."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from openpyxl import Workbook
from openpyxl.styles import Font
from pypdf import PdfReader


DEFAULT_OUTPUT_DIR = Path("artifacts/pdf_qa_extraction")
OUTPUT_JSONL = "pdf_qa_candidates.jsonl"
OUTPUT_XLSX = "pdf_qa_candidates_review.xlsx"
OUTPUT_REPORT = "pdf_qa_extraction_report.md"

LIST_TERMS = ("持参", "携帯", "準備", "グッズ", "携行", "装備")
MEASURE_TERMS = ("整備", "設置", "支援", "指定", "計画", "対策", "実施", "促進", "強化")
LESSON_TERMS = ("注意", "教訓", "重要", "必要", "備え", "留意", "防ぐ")
FORBIDDEN_FOR_CARRY = ("防災行政無線", "山小屋の屋根", "避難路", "ロープ設置", "標識", "退避壕", "避難施設")
EXPECTED_ITEM_TERMS = (
    "ヘルメット",
    "ゴーグル",
    "マスク",
    "ヘッドライト",
    "懐中電灯",
    "雨具",
    "タオル",
    "非常食",
    "飲料水",
    "携帯電話",
    "予備電源",
    "登山地図",
    "コンパス",
)
MEASURE_EXPECTED_TERMS = (
    "防災行政無線",
    "避難施設",
    "避難路",
    "退避壕",
    "退避舎",
    "ビジターセンター",
    "避難促進施設",
    "計画",
    "指定",
)


@dataclass(frozen=True)
class PageText:
    page: int
    text: str


def normalize_text(text: str) -> str:
    text = text.replace("\x00", "").replace("\u0001", " ")
    text = re.sub(r"[\x02-\x08\x0b\x0c\x0e-\x1f]", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def compact(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def truncate(text: str, max_chars: int = 240) -> str:
    text = compact(text)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def split_sentences(text: str) -> list[str]:
    text = normalize_text(text)
    raw_parts = re.split(r"(?<=[。！？!?])\s*|\n+", text)
    out = []
    for part in raw_parts:
        part = compact(part)
        if len(part) < 18:
            continue
        if re.fullmatch(r"[\d\s\-–—年月日]+", part):
            continue
        out.append(part)
    return out


def extract_pdf_pages(pdf_path: Path) -> list[PageText]:
    reader = PdfReader(str(pdf_path))
    pages: list[PageText] = []
    for index, page in enumerate(reader.pages, start=1):
        text = normalize_text(page.extract_text() or "")
        if text:
            pages.append(PageText(page=index, text=text))
    return pages


def unique_terms(terms: Iterable[str]) -> list[str]:
    out = []
    for term in terms:
        term = str(term or "").strip()
        if term and term not in out:
            out.append(term)
    return out


def terms_in_text(text: str, terms: Iterable[str]) -> list[str]:
    return unique_terms(term for term in terms if term in text)


def numeric_terms(text: str) -> list[str]:
    patterns = [
        r"令和\d+年",
        r"\d+(?:,\d{3})*(?:\.\d+)?\s*(?:火山|件|個|名|年|月|日|時間|％|%|箇所|か所|円)",
        r"\d+(?:,\d{3})*(?:\.\d+)?",
    ]
    found: list[str] = []
    for pattern in patterns:
        found.extend(match.strip() for match in re.findall(pattern, text))
    return unique_terms(found)[:5]


def confidence_for(question_type: str, expected_all: list[str], quote: str) -> str:
    if question_type == "list_items" and len(expected_all) >= 3:
        return "high"
    if question_type == "count_fact" and expected_all:
        return "medium"
    if question_type in {"measure", "definition"} and expected_all:
        return "medium"
    if len(quote) > 180:
        return "low"
    return "medium"


def build_candidate(
    *,
    question: str,
    expected_answer: str,
    source_page: int,
    source_quote: str,
    question_type: str,
    expected_all: list[str] | None = None,
    expected_any: list[str] | None = None,
    forbidden_any: list[str] | None = None,
) -> dict:
    expected_all = unique_terms(expected_all or [])
    expected_any = unique_terms(expected_any or [])
    forbidden_any = unique_terms(forbidden_any or [])
    return {
        "case_id": "",
        "question": question,
        "expected_answer": expected_answer,
        "source_page": source_page,
        "source_quote": source_quote,
        "question_type": question_type,
        "expected_all": expected_all,
        "expected_any": expected_any,
        "expected_min_hits": 1,
        "forbidden_any": forbidden_any,
        "confidence": confidence_for(question_type, expected_all or expected_any, source_quote),
        "review_status": "needs_review",
        "notes": "",
    }


def make_count_candidate(sentence: str, page: int) -> dict | None:
    nums = numeric_terms(sentence)
    if not nums:
        return None
    quote = truncate(sentence)
    focus = nums[0]
    question = f"本文では「{focus}」に関してどのような事実が示されていますか。"
    return build_candidate(
        question=question,
        expected_answer=quote,
        source_page=page,
        source_quote=quote,
        question_type="count_fact",
        expected_all=[focus],
        expected_any=nums[1:],
    )


def make_definition_candidate(sentence: str, page: int) -> dict | None:
    if "とは" not in sentence and "定義" not in sentence and "意味" not in sentence:
        return None
    quote = truncate(sentence)
    subject = ""
    match = re.search(r"([「『]?[A-Za-z0-9ぁ-んァ-ン一-龥ー・（）()]{2,30}[」』]?)とは", sentence)
    if match:
        subject = match.group(1).strip("「」『』")
    question = f"{subject}とは何ですか。" if subject else "本文ではどのような定義が説明されていますか。"
    expected = [subject] if subject else []
    return build_candidate(
        question=question,
        expected_answer=quote,
        source_page=page,
        source_quote=quote,
        question_type="definition",
        expected_all=expected,
    )


def make_list_candidate(sentence: str, page: int) -> dict | None:
    if not any(term in sentence for term in LIST_TERMS):
        return None
    quote = truncate(sentence)
    items = terms_in_text(sentence, EXPECTED_ITEM_TERMS)
    expected_all = items[:5] if items else terms_in_text(sentence, LIST_TERMS)[:3]
    forbidden = terms_in_text(sentence, FORBIDDEN_FOR_CARRY)
    question = "本文では、持参・準備すべきものとして何が挙げられていますか。"
    return build_candidate(
        question=question,
        expected_answer=quote,
        source_page=page,
        source_quote=quote,
        question_type="list_items",
        expected_all=expected_all,
        expected_any=items[5:],
        forbidden_any=forbidden,
    )


def make_measure_candidate(sentence: str, page: int) -> dict | None:
    if not any(term in sentence for term in MEASURE_TERMS):
        return None
    quote = truncate(sentence)
    expected = terms_in_text(sentence, MEASURE_EXPECTED_TERMS)
    if not expected:
        expected = terms_in_text(sentence, MEASURE_TERMS)[:3]
    question = "本文では、どのような施策・対策が示されていますか。"
    return build_candidate(
        question=question,
        expected_answer=quote,
        source_page=page,
        source_quote=quote,
        question_type="measure",
        expected_all=expected[:5],
        expected_any=expected[5:],
    )


def make_lesson_candidate(sentence: str, page: int) -> dict | None:
    if not any(term in sentence for term in LESSON_TERMS):
        return None
    quote = truncate(sentence)
    expected = terms_in_text(sentence, LESSON_TERMS)[:3]
    question = "本文では、どのような注意点や教訓が示されていますか。"
    return build_candidate(
        question=question,
        expected_answer=quote,
        source_page=page,
        source_quote=quote,
        question_type="other",
        expected_all=expected,
    )


def generate_candidates(pages: list[PageText], max_candidates: int) -> list[dict]:
    candidates: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    makers = (
        make_count_candidate,
        make_definition_candidate,
        make_list_candidate,
        make_measure_candidate,
        make_lesson_candidate,
    )

    for page in pages:
        per_page = 0
        for sentence in split_sentences(page.text):
            for maker in makers:
                candidate = maker(sentence, page.page)
                if candidate is None:
                    continue
                key = (candidate["question_type"], candidate["question"], candidate["source_quote"])
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(candidate)
                per_page += 1
                if len(candidates) >= max_candidates:
                    return assign_case_ids(candidates)
                if per_page >= 4:
                    break
            if per_page >= 4:
                break

    if not candidates:
        for page in pages:
            sentences = split_sentences(page.text)
            if not sentences:
                continue
            quote = truncate(sentences[0])
            candidates.append(
                build_candidate(
                    question="このページでは何が説明されていますか。",
                    expected_answer=quote,
                    source_page=page.page,
                    source_quote=quote,
                    question_type="summary",
                    expected_all=[],
                    expected_any=[],
                )
            )
            if len(candidates) >= max_candidates:
                break

    return assign_case_ids(candidates)


def assign_case_ids(candidates: list[dict]) -> list[dict]:
    for index, candidate in enumerate(candidates, start=1):
        candidate["case_id"] = f"auto_q{index:03d}"
    return candidates


def write_jsonl(path: Path, candidates: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as file:
        for candidate in candidates:
            file.write(json.dumps(candidate, ensure_ascii=False) + "\n")


def write_excel(path: Path, candidates: list[dict]) -> None:
    headers = [
        "case_id",
        "review_status",
        "question",
        "expected_answer",
        "source_page",
        "source_quote",
        "question_type",
        "expected_all",
        "expected_any",
        "expected_min_hits",
        "forbidden_any",
        "confidence",
        "notes",
    ]
    wb = Workbook()
    ws = wb.active
    ws.title = "qa_candidates"
    ws.append(headers)
    for candidate in candidates:
        row = []
        for header in headers:
            value = candidate.get(header, "")
            if isinstance(value, list):
                value = json.dumps(value, ensure_ascii=False)
            row.append(value)
        ws.append(row)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = "A2"
    widths = {
        "A": 14,
        "B": 16,
        "C": 48,
        "D": 64,
        "E": 12,
        "F": 64,
        "G": 18,
        "H": 32,
        "I": 32,
        "J": 16,
        "K": 32,
        "L": 14,
        "M": 32,
    }
    for column, width in widths.items():
        ws.column_dimensions[column].width = width
    wb.save(path)


def write_report(path: Path, input_pdf: Path, pages: list[PageText], candidates: list[dict], output_dir: Path) -> None:
    by_type = Counter(candidate["question_type"] for candidate in candidates)
    by_confidence = Counter(candidate["confidence"] for candidate in candidates)
    lines = [
        "# PDF QA Extraction Report",
        "",
        f"- input_pdf: `{input_pdf}`",
        f"- pages_extracted: {len(pages)}",
        f"- candidates_count: {len(candidates)}",
        "",
        "## Count By Question Type",
        "",
    ]
    for key, count in sorted(by_type.items()):
        lines.append(f"- {key}: {count}")
    lines.extend(["", "## Count By Confidence", ""])
    for key, count in sorted(by_confidence.items()):
        lines.append(f"- {key}: {count}")
    lines.extend(
        [
            "",
            "## Output Files",
            "",
            f"- `{output_dir / OUTPUT_JSONL}`",
            f"- `{output_dir / OUTPUT_XLSX}`",
            f"- `{output_dir / OUTPUT_REPORT}`",
            "",
            "## Next Step",
            "",
            "1. Excelを人間が確認",
            "2. reviewedだけ fixed_qa_cases.reviewed.jsonl に変換",
            "3. run_fixed_qa_regression_eval.py で突合評価",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate reviewable QA candidates from a PDF.")
    parser.add_argument("--pdf", type=Path, required=True, help="Input PDF path.")
    parser.add_argument("--max-candidates", type=int, default=50, help="Maximum candidates to generate.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory. Default: {DEFAULT_OUTPUT_DIR}",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.pdf.exists():
        raise SystemExit(f"PDF not found: {args.pdf}")
    if args.max_candidates <= 0:
        raise SystemExit("--max-candidates must be positive")

    pages = extract_pdf_pages(args.pdf)
    candidates = generate_candidates(pages, args.max_candidates)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = args.output_dir / OUTPUT_JSONL
    xlsx_path = args.output_dir / OUTPUT_XLSX
    report_path = args.output_dir / OUTPUT_REPORT

    write_jsonl(jsonl_path, candidates)
    write_excel(xlsx_path, candidates)
    write_report(report_path, args.pdf, pages, candidates, args.output_dir)

    print(f"input_pdf: {args.pdf}")
    print(f"pages_extracted: {len(pages)}")
    print(f"candidates_count: {len(candidates)}")
    print("question_type_counts:")
    for key, count in sorted(Counter(candidate["question_type"] for candidate in candidates).items()):
        print(f"- {key}: {count}")
    print("confidence_counts:")
    for key, count in sorted(Counter(candidate["confidence"] for candidate in candidates).items()):
        print(f"- {key}: {count}")
    print("output_files:")
    print(f"- {jsonl_path}")
    print(f"- {xlsx_path}")
    print(f"- {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
