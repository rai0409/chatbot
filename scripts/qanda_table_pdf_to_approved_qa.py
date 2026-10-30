from __future__ import annotations

# --- bootstrap: add repo root to sys.path for script execution ---
import sys
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# --- end bootstrap ---

import argparse
import dataclasses
import hashlib
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from rag_core.approved_qa import validate_approved_qa_records
from rag_core.question_normalization import normalize_question_for_exact_match


# Document title and id prefix are per document (pass --title and
# --chunk-id-prefix); nothing project-specific is hard-coded.
DEFAULT_TITLE = ""
DEFAULT_CHUNK_ID_PREFIX = "qanda_q"
ALLOWED_STATUSES = {"draft", "approved", "rejected"}
# This converter only produces drafts: approval is a separate human review
# step (scripts/approved_qa_review.py), never a side effect of extraction.
GENERATION_STATUSES = {"draft"}
JP_CHAR_RE = re.compile(r"[぀-ヿ㐀-鿿々〆〤]")
_HEADER_WORDS = {"No", "NO", "№", "質問項目", "質問内容", "回答"}
_LINE_MERGE_TOLERANCE = 1.5


@dataclass(frozen=True)
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    page: int

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass(frozen=True)
class QARow:
    page: int
    question_no: int
    question_item: str
    question: str
    answer: str


def _compact(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def cleanup_text(text: str) -> str:
    """Normalize PDF-extracted table cell text without semantic rewriting."""
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("　", " ")
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(rf"(?<={JP_CHAR_RE.pattern})\s+(?={JP_CHAR_RE.pattern})", "", text)
    text = re.sub(r"\s+([。、，,.!?！？:：;；)）】」])", r"\1", text)
    text = re.sub(r"([（(【「])\s+", r"\1", text)
    text = re.sub(rf"([」）])\s+({JP_CHAR_RE.pattern})", r"\1\2", text)
    return text.strip()


def _line_text(words: Sequence[Word], *, y_tolerance: float = 4.0) -> str:
    if not words:
        return ""
    lines: List[List[Word]] = []
    for word in sorted(words, key=lambda w: (w.y0, w.x0)):
        if not lines or abs(word.y0 - lines[-1][0].y0) > y_tolerance:
            lines.append([word])
        else:
            lines[-1].append(word)
    line_texts = [" ".join(w.text for w in sorted(line, key=lambda w: w.x0)) for line in lines]
    return cleanup_text(" ".join(line_texts))


def _is_row_number_word(word: Word, *, item_x: float) -> bool:
    text = cleanup_text(word.text)
    return word.x0 < item_x and bool(re.fullmatch(r"\d{1,3}", text))


def _row_bounds(number_words: Sequence[Word], *, header_bottom_y: float, page_bottom_y: float) -> List[tuple[Word, float, float]]:
    sorted_numbers = sorted(number_words, key=lambda w: (w.cy, w.x0))
    bounds: List[tuple[Word, float, float]] = []
    for idx, number in enumerate(sorted_numbers):
        if idx == 0:
            top = header_bottom_y
        else:
            top = (sorted_numbers[idx - 1].cy + number.cy) / 2
        if idx + 1 < len(sorted_numbers):
            bottom = (number.cy + sorted_numbers[idx + 1].cy) / 2
        else:
            bottom = page_bottom_y
        bounds.append((number, top, bottom))
    return bounds


def _header_bottom_y(words: Sequence[Word]) -> float:
    header_words = [word for word in words if cleanup_text(word.text) in _HEADER_WORDS]
    if header_words:
        return max(word.y1 for word in header_words) + 3
    return 70.0


def _merge_line_positions(ys: Iterable[float]) -> List[float]:
    merged: List[float] = []
    for y in sorted(ys):
        if merged and abs(y - merged[-1]) <= _LINE_MERGE_TOLERANCE:
            continue
        merged.append(y)
    return merged


def _cells(words: Sequence[Word], *, item_x: float, question_x: float, answer_x: float) -> Dict[str, str]:
    return {
        "item": _line_text([w for w in words if item_x <= w.x0 < question_x]),
        "question": _line_text([w for w in words if question_x <= w.x0 < answer_x]),
        "answer": _line_text([w for w in words if w.x0 >= answer_x]),
    }


def _join_cell(first: str, second: str) -> str:
    return cleanup_text(" ".join(part for part in (first, second) if part))


def words_to_rows_detailed(
    words: Sequence[Word],
    *,
    item_x: float = 70.0,
    question_x: float = 155.0,
    answer_x: float = 470.0,
    page_bottom_y: float = 595.0,
    hlines: Optional[Dict[int, Sequence[float]]] = None,
) -> Dict[str, Any]:
    """Group words into numbered rows, page by page.

    With ruling lines (hlines: page -> y positions of horizontal lines) a row
    is the band between two lines, so a vertically centred number cell or a
    multi-line answer no longer bleeds into the neighbouring row. Without
    lines the historical midpoint rule is used.

    Returns rows (including incomplete ones, flagged by empty cells),
    `leading` text found above the first numbered row of each page (the
    continuation of the previous page's last row) and `dropped` diagnostics.
    """
    by_page: Dict[int, List[Word]] = {}
    for word in words:
        if cleanup_text(word.text):
            by_page.setdefault(word.page, []).append(word)

    rows: List[QARow] = []
    leading: Dict[int, Dict[str, str]] = {}
    dropped: List[Dict[str, Any]] = []
    for page, page_words in sorted(by_page.items()):
        header_bottom = _header_bottom_y(page_words)
        has_header = any(cleanup_text(w.text) in _HEADER_WORDS for w in page_words)
        number_words = [word for word in page_words if _is_row_number_word(word, item_x=item_x)]
        # Lines above the header row are page decoration; a continuation page
        # without a header row keeps every line (its first band continues the
        # previous page's last row).
        min_line_y = header_bottom - 4 if has_header else 0.0
        page_lines = _merge_line_positions(
            y for y in (hlines or {}).get(page, []) if min_line_y <= y <= page_bottom_y
        )
        bands: List[tuple[Optional[Word], float, float]] = []
        if len(page_lines) >= 2:
            for top, bottom in zip(page_lines, page_lines[1:]):
                inside = [w for w in number_words if top <= w.cy < bottom]
                bands.append((min(inside, key=lambda w: w.cy) if inside else None, top, bottom))
        else:
            bands = [(n, t, b) for n, t, b in _row_bounds(number_words, header_bottom_y=header_bottom, page_bottom_y=page_bottom_y)]

        seen_number = False
        for number_word, top, bottom in bands:
            band_words = [
                w for w in page_words
                if top <= w.cy < bottom and w is not number_word and cleanup_text(w.text) not in _HEADER_WORDS
            ]
            cells = _cells(band_words, item_x=item_x, question_x=question_x, answer_x=answer_x)
            if number_word is None:
                if not any(cells.values()):
                    continue
                if not seen_number:
                    leading[page] = cells
                elif rows and rows[-1].page == page:
                    last = rows[-1]
                    rows[-1] = dataclasses.replace(
                        last,
                        question_item=_join_cell(last.question_item, cells["item"]),
                        question=_join_cell(last.question, cells["question"]),
                        answer=_join_cell(last.answer, cells["answer"]),
                    )
                continue
            seen_number = True
            try:
                question_no = int(cleanup_text(number_word.text))
            except ValueError:
                dropped.append({"page": page, "reason": "invalid_row_number"})
                continue
            rows.append(
                QARow(
                    page=page,
                    question_no=question_no,
                    question_item=cells["item"],
                    question=cells["question"],
                    answer=cells["answer"],
                )
            )
    return {"rows": rows, "leading": leading, "dropped": dropped}


def merge_page_continuations(rows: Sequence[QARow], leading: Dict[int, Dict[str, str]]) -> List[QARow]:
    """Append text found at the top of a page to the last row of the previous page."""
    merged: List[QARow] = []
    for row in rows:
        if merged and row.page != merged[-1].page:
            cont = leading.get(row.page)
            if cont:
                last = merged[-1]
                merged[-1] = dataclasses.replace(
                    last,
                    question_item=_join_cell(last.question_item, cont["item"]),
                    question=_join_cell(last.question, cont["question"]),
                    answer=_join_cell(last.answer, cont["answer"]),
                )
                leading = {k: v for k, v in leading.items() if k != row.page}
        merged.append(row)
    if merged:
        # A continuation page that has no numbered row of its own.
        for page in sorted(p for p in leading if p > merged[-1].page):
            cont = leading[page]
            last = merged[-1]
            merged[-1] = dataclasses.replace(
                last,
                question_item=_join_cell(last.question_item, cont["item"]),
                question=_join_cell(last.question, cont["question"]),
                answer=_join_cell(last.answer, cont["answer"]),
            )
    return merged


def words_to_rows(
    words: Sequence[Word],
    *,
    item_x: float = 70.0,
    question_x: float = 155.0,
    answer_x: float = 470.0,
    page_bottom_y: float = 595.0,
    hlines: Optional[Dict[int, Sequence[float]]] = None,
) -> List[QARow]:
    detailed = words_to_rows_detailed(
        words,
        item_x=item_x,
        question_x=question_x,
        answer_x=answer_x,
        page_bottom_y=page_bottom_y,
        hlines=hlines,
    )
    rows = merge_page_continuations(detailed["rows"], detailed["leading"])
    return [row for row in rows if row.question and row.answer]


def _stable_qa_id(
    *,
    tenant_id: str,
    source_doc: str,
    question_no: int,
    normalized_question: str,
    answer: str,
) -> str:
    answer_hash = hashlib.sha256(answer.encode("utf-8")).hexdigest()[:16]
    payload = f"{tenant_id}\n{source_doc}\n{question_no}\n{normalized_question}\n{answer_hash}".encode("utf-8")
    return "qa_" + hashlib.sha256(payload).hexdigest()[:16]


def row_to_record(
    row: QARow,
    *,
    source_doc: str,
    tenant_id: str,
    doc_version: str,
    status: str = "draft",
    created_at: str | None = None,
    title: str = DEFAULT_TITLE,
    chunk_id_prefix: str = DEFAULT_CHUNK_ID_PREFIX,
) -> dict:
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"invalid status: {status}")
    normalized = normalize_question_for_exact_match(row.question)
    timestamp = created_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return {
        "qa_id": _stable_qa_id(
            tenant_id=tenant_id,
            source_doc=source_doc,
            question_no=row.question_no,
            normalized_question=normalized,
            answer=row.answer,
        ),
        "question": row.question,
        "normalized_question": normalized,
        "approved_answer": row.answer,
        "approved_citations": [
            {
                "source_doc": source_doc,
                "source_pages": [row.page],
                "chunk_id": f"{chunk_id_prefix}{row.question_no:03d}",
                "title": title or source_doc,
            }
        ],
        "tags": ["qanda_table", "candidate"],
        "language": "ja",
        "tenant_id": tenant_id,
        "doc_version": doc_version,
        "status": status,
        "created_at": timestamp,
        "notes": "generated_from_qanda_table_pdf",
        "source_question_no": row.question_no,
        "question_item": row.question_item,
    }


def dedupe_records(records: Iterable[dict]) -> tuple[List[dict], int]:
    out: List[dict] = []
    seen: set[tuple[str, str]] = set()
    duplicate_count = 0
    for record in records:
        key = (
            cleanup_text(record.get("tenant_id")) or "default",
            cleanup_text(record.get("normalized_question")),
        )
        if key in seen:
            duplicate_count += 1
            continue
        seen.add(key)
        out.append(record)
    return out, duplicate_count


def validate_records(records: Sequence[dict]) -> List[str]:
    errors = validate_approved_qa_records(list(records))
    seen: set[tuple[str, str]] = set()
    for idx, record in enumerate(records, start=1):
        for key in (
            "qa_id",
            "question",
            "normalized_question",
            "approved_answer",
            "status",
            "source_question_no",
        ):
            if record.get(key) in (None, ""):
                errors.append(f"line {idx}: missing {key}")
        citations = record.get("approved_citations")
        if not isinstance(citations, list) or not citations:
            errors.append(f"line {idx}: missing approved_citations")
        status = cleanup_text(record.get("status"))
        if status not in ALLOWED_STATUSES:
            errors.append(f"line {idx}: invalid status: {status}")
        key = (cleanup_text(record.get("tenant_id")) or "default", cleanup_text(record.get("normalized_question")))
        if key in seen:
            errors.append(f"line {idx}: duplicate normalized_question for tenant")
        seen.add(key)
    return errors


def write_jsonl(path: str | Path, records: Iterable[dict]) -> None:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def format_debug_words(words: Sequence[Word], *, limit: int = 80) -> str:
    lines: List[str] = []
    for word in list(words)[: max(0, limit)]:
        lines.append(f"{word.page}\t{word.x0:.1f}\t{word.y0:.1f}\t{word.x1:.1f}\t{word.y1:.1f}\t{word.text}")
    if len(words) > limit:
        lines.append(f"... truncated {len(words) - limit} more words")
    return "\n".join(lines)


def _page_hlines(page) -> List[float]:
    # Horizontal ruling lines: line segments with (almost) equal y, and the
    # top/bottom edges of thin rectangles that PDFs often use as borders
    # (docextract.pdf, pdfplumber).
    try:
        return page.horizontal_rules()
    except Exception:
        return []


def extract_pdf_words(pdf_path: str | Path, *, debug_page: int | None = None) -> tuple[List[Word], int, dict[int, float]]:
    words, page_count, page_bottoms, _hlines = extract_pdf_layout(pdf_path, debug_page=debug_page)
    return words, page_count, page_bottoms


def extract_pdf_layout(
    pdf_path: str | Path, *, debug_page: int | None = None
) -> tuple[List[Word], int, dict[int, float], dict[int, List[float]]]:
    # pdfplumber + pypdfium2 through libs/docextract (PyMuPDF, AGPL-3.0, is
    # no longer used). Coordinates are points from the top-left, as before.
    from rag_core import extraction

    dx = extraction.load()
    words: List[Word] = []
    page_bottoms: dict[int, float] = {}
    hlines: dict[int, List[float]] = {}
    try:
        with dx.open_pdf(pdf_path) as doc:
            page_count = len(doc)
            for page in doc.pages:
                page_num = page.number
                page_bottoms[page_num] = page.height
                if debug_page is not None and page_num != debug_page:
                    page.close()
                    continue
                hlines[page_num] = _page_hlines(page)
                for x0, y0, x1, y1, text in page.words():
                    words.append(Word(x0, y0, x1, y1, text, page_num))
                page.close()
    except dx.PdfUnavailable as exc:
        raise RuntimeError(str(exc)) from exc
    return words, page_count, page_bottoms, hlines


def convert_pdf(
    *,
    pdf_path: str | Path,
    output_path: str | Path,
    source_doc: str,
    tenant_id: str = "default",
    doc_version: str = "v1",
    status: str = "draft",
    item_x: float = 70.0,
    question_x: float = 155.0,
    answer_x: float = 470.0,
    title: str = DEFAULT_TITLE,
    chunk_id_prefix: str = DEFAULT_CHUNK_ID_PREFIX,
    use_ruling_lines: bool = True,
    debug_words: bool = False,
    debug_page: int | None = None,
) -> dict:
    if status not in GENERATION_STATUSES:
        raise ValueError(f"this converter only writes drafts (got status={status}); approve via review")
    words, page_count, page_bottoms, hlines = extract_pdf_layout(
        pdf_path, debug_page=debug_page if debug_words else None
    )
    if debug_words:
        print(format_debug_words(words))
        if debug_page is not None:
            return {
                "pages": page_count,
                "raw_rows": 0,
                "written": 0,
                "skipped": 0,
                "duplicate_count": 0,
                "output_path": str(output_path),
            }

    all_rows: List[QARow] = []
    leading: Dict[int, Dict[str, str]] = {}
    dropped: List[Dict[str, Any]] = []
    for page in sorted({word.page for word in words}):
        page_words = [word for word in words if word.page == page]
        detailed = words_to_rows_detailed(
            page_words,
            item_x=item_x,
            question_x=question_x,
            answer_x=answer_x,
            page_bottom_y=page_bottoms.get(page, 595.0),
            hlines=hlines if use_ruling_lines else None,
        )
        all_rows.extend(detailed["rows"])
        leading.update(detailed["leading"])
        dropped.extend(detailed["dropped"])

    merged = merge_page_continuations(all_rows, leading)
    complete: List[QARow] = []
    for row in merged:
        if row.question and row.answer:
            complete.append(row)
        else:
            dropped.append(
                {
                    "page": row.page,
                    "question_no": row.question_no,
                    "reason": "missing_answer" if row.question else "missing_question",
                }
            )

    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    raw_records = [
        row_to_record(
            row,
            source_doc=source_doc,
            tenant_id=tenant_id,
            doc_version=doc_version,
            status=status,
            created_at=timestamp,
            title=title,
            chunk_id_prefix=chunk_id_prefix,
        )
        for row in complete
    ]
    records, duplicate_count = dedupe_records(raw_records)
    errors = validate_records(records)
    if errors:
        raise ValueError("invalid generated approved QA records: " + "; ".join(errors))
    write_jsonl(output_path, records)

    status_counts = Counter(record.get("status") for record in records)
    drop_reasons = Counter(item["reason"] for item in dropped)
    summary = {
        "pages": page_count,
        "raw_rows": len(merged),
        "written": len(records),
        "skipped": len(merged) - len(records),
        "duplicate_count": duplicate_count,
        "dropped_rows": len(dropped),
        "dropped_by_reason": dict(sorted(drop_reasons.items())),
        "dropped_detail": dropped[:50],
        "pages_with_ruling_lines": sum(1 for ys in hlines.values() if len(ys) >= 2) if use_ruling_lines else 0,
        "status_counts": dict(status_counts),
        "output_path": str(output_path),
    }
    print(
        "Summary: "
        f"pages={summary['pages']} "
        f"raw_rows={summary['raw_rows']} "
        f"written={summary['written']} "
        f"skipped={summary['skipped']} "
        f"dropped_rows={summary['dropped_rows']} "
        f"duplicate_count={summary['duplicate_count']} "
        f"status_counts={summary['status_counts']} "
        f"output_path={summary['output_path']}"
    )
    if summary["dropped_rows"]:
        print(f"WARN: dropped rows by reason: {summary['dropped_by_reason']}")
    if summary["written"] < 5:
        print("WARN: extracted Q&A row count is unexpectedly low; tune --item-x/--question-x/--answer-x if needed.")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert Japanese Q&A table PDFs to draft approved-QA JSONL.")
    parser.add_argument("--pdf", required=True, dest="pdf_path")
    parser.add_argument("--out", required=True, dest="output_path")
    parser.add_argument("--source-doc", required=True)
    parser.add_argument("--tenant-id", default="default")
    parser.add_argument("--doc-version", default="v1")
    parser.add_argument("--status", default="draft", choices=sorted(GENERATION_STATUSES))
    parser.add_argument("--title", default=DEFAULT_TITLE, help="document title shown in citations")
    parser.add_argument("--chunk-id-prefix", default=DEFAULT_CHUNK_ID_PREFIX)
    parser.add_argument("--item-x", type=float, default=70.0)
    parser.add_argument("--question-x", type=float, default=155.0)
    parser.add_argument("--answer-x", type=float, default=470.0)
    parser.add_argument("--no-ruling-lines", action="store_true", help="ignore table borders; use the midpoint rule")
    parser.add_argument("--debug-words", action="store_true")
    parser.add_argument("--debug-page", type=int)
    args = parser.parse_args()
    try:
        convert_pdf(
            pdf_path=args.pdf_path,
            output_path=args.output_path,
            source_doc=args.source_doc,
            tenant_id=args.tenant_id,
            doc_version=args.doc_version,
            status=args.status,
            chunk_id_prefix=args.chunk_id_prefix,
            item_x=args.item_x,
            question_x=args.question_x,
            answer_x=args.answer_x,
            title=args.title,
            use_ruling_lines=not args.no_ruling_lines,
            debug_words=args.debug_words,
            debug_page=args.debug_page,
        )
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
