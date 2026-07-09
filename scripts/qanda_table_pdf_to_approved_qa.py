from __future__ import annotations

# --- bootstrap: add repo root to sys.path for script execution ---
import sys
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# --- end bootstrap ---

import argparse
import hashlib
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, List, Sequence

from rag_core.approved_qa import validate_approved_qa_records
from rag_core.question_normalization import normalize_question_for_exact_match


TITLE = "観光デジタルアンケート分析業務 質問に対する回答"
DEFAULT_CHUNK_ID_PREFIX = "tourism_q"
ALLOWED_STATUSES = {"draft", "approved", "rejected"}
JP_CHAR_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff々〆〤]")


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
    text = text.replace("\u3000", " ")
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
    header_words = [
        word
        for word in words
        if cleanup_text(word.text) in {"No", "NO", "№", "質問項目", "質問内容", "回答"}
    ]
    if header_words:
        return max(word.y1 for word in header_words) + 3
    return 70.0


def words_to_rows(
    words: Sequence[Word],
    *,
    item_x: float = 70.0,
    question_x: float = 155.0,
    answer_x: float = 470.0,
    page_bottom_y: float = 595.0,
) -> List[QARow]:
    by_page: dict[int, List[Word]] = {}
    for word in words:
        if cleanup_text(word.text):
            by_page.setdefault(word.page, []).append(word)

    rows: List[QARow] = []
    for page, page_words in sorted(by_page.items()):
        header_bottom = _header_bottom_y(page_words)
        number_words = [word for word in page_words if _is_row_number_word(word, item_x=item_x)]
        for number_word, top, bottom in _row_bounds(
            number_words,
            header_bottom_y=header_bottom,
            page_bottom_y=page_bottom_y,
        ):
            row_words = [
                word
                for word in page_words
                if top <= word.cy < bottom and cleanup_text(word.text) != cleanup_text(number_word.text)
            ]
            item_words = [word for word in row_words if item_x <= word.x0 < question_x]
            question_words = [word for word in row_words if question_x <= word.x0 < answer_x]
            answer_words = [word for word in row_words if word.x0 >= answer_x]
            try:
                question_no = int(cleanup_text(number_word.text))
            except Exception:
                continue
            question_item = _line_text(item_words)
            question = _line_text(question_words)
            answer = _line_text(answer_words)
            if question and answer:
                rows.append(
                    QARow(
                        page=page,
                        question_no=question_no,
                        question_item=question_item,
                        question=question,
                        answer=answer,
                    )
                )
    return rows


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
    title: str = TITLE,
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
                "title": title,
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


def extract_pdf_words(pdf_path: str | Path, *, debug_page: int | None = None) -> tuple[List[Word], int, dict[int, float]]:
    try:
        import fitz  # type: ignore
    except Exception as exc:
        raise RuntimeError("PyMuPDF is required. Install requirements-pdf.txt.") from exc

    words: List[Word] = []
    page_bottoms: dict[int, float] = {}
    with fitz.open(pdf_path) as doc:
        page_count = len(doc)
        for page_idx, page in enumerate(doc):
            page_num = page_idx + 1
            page_bottoms[page_num] = float(page.rect.y1)
            if debug_page is not None and page_num != debug_page:
                continue
            for raw in page.get_text("words"):
                x0, y0, x1, y1, text, *_ = raw
                words.append(Word(float(x0), float(y0), float(x1), float(y1), str(text), page_num))
    return words, page_count, page_bottoms


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
    title: str = TITLE,
    chunk_id_prefix: str = DEFAULT_CHUNK_ID_PREFIX,
    debug_words: bool = False,
    debug_page: int | None = None,
) -> dict:
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"invalid status: {status}")
    words, page_count, page_bottoms = extract_pdf_words(pdf_path, debug_page=debug_page if debug_words else None)
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
    for page in sorted({word.page for word in words}):
        page_words = [word for word in words if word.page == page]
        all_rows.extend(
            words_to_rows(
                page_words,
                item_x=item_x,
                question_x=question_x,
                answer_x=answer_x,
                page_bottom_y=page_bottoms.get(page, 595.0),
            )
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
        for row in all_rows
    ]
    records, duplicate_count = dedupe_records(raw_records)
    errors = validate_records(records)
    if errors:
        raise ValueError("invalid generated approved QA records: " + "; ".join(errors))
    write_jsonl(output_path, records)

    status_counts = Counter(record.get("status") for record in records)
    summary = {
        "pages": page_count,
        "raw_rows": len(all_rows),
        "written": len(records),
        "skipped": len(all_rows) - len(records),
        "duplicate_count": duplicate_count,
        "status_counts": dict(status_counts),
        "output_path": str(output_path),
    }
    print(
        "Summary: "
        f"pages={summary['pages']} "
        f"raw_rows={summary['raw_rows']} "
        f"written={summary['written']} "
        f"skipped={summary['skipped']} "
        f"duplicate_count={summary['duplicate_count']} "
        f"status_counts={summary['status_counts']} "
        f"output_path={summary['output_path']}"
    )
    if summary["written"] < 5:
        print("WARN: extracted Q&A row count is unexpectedly low; tune --item-x/--question-x/--answer-x if needed.")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert Japanese Q&A table PDFs to approved-QA JSONL.")
    parser.add_argument("--pdf", required=True, dest="pdf_path")
    parser.add_argument("--out", required=True, dest="output_path")
    parser.add_argument("--source-doc", required=True)
    parser.add_argument("--tenant-id", default="default")
    parser.add_argument("--doc-version", default="v1")
    parser.add_argument("--status", default="draft", choices=["draft", "approved", "rejected"])
    parser.add_argument("--title", default=TITLE)
    parser.add_argument("--chunk-id-prefix", default=DEFAULT_CHUNK_ID_PREFIX)
    parser.add_argument("--item-x", type=float, default=70.0)
    parser.add_argument("--question-x", type=float, default=155.0)
    parser.add_argument("--answer-x", type=float, default=470.0)
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
            debug_words=args.debug_words,
            debug_page=args.debug_page,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
