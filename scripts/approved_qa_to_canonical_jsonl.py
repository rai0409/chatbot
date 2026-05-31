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
from pathlib import Path
from typing import Any, Iterable, List

from rag_core.approved_qa import validate_approved_qa_records
from rag_core.question_normalization import normalize_question_for_exact_match


DOC_TYPE = "approved_qa_pair"
CHUNK_TYPE = "qa_pair"


def _compact(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _read_jsonl(path: str | Path) -> List[dict]:
    records: List[dict] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if raw:
                obj = json.loads(raw)
                if isinstance(obj, dict):
                    records.append(obj)
                else:
                    raise ValueError("approved QA JSONL records must be objects")
    return records


def _source_pages(value: Any) -> List[int]:
    if value is None:
        return [-1]
    raw_items = value if isinstance(value, list) else [value]
    pages: List[int] = []
    for item in raw_items:
        if isinstance(item, bool):
            continue
        try:
            pages.append(int(item))
        except Exception:
            continue
    return pages or [-1]


def _first_citation(record: dict) -> dict:
    citations = record.get("approved_citations")
    if isinstance(citations, list):
        for citation in citations:
            if isinstance(citation, dict):
                return citation
    return {}


def _stable_id(record: dict, normalized_question: str, answer: str) -> str:
    qa_id = _compact(record.get("qa_id"))
    if qa_id:
        return f"approved_qa_pair:{qa_id}"
    payload = "\n".join(
        [
            _compact(record.get("tenant_id")) or "default",
            normalized_question,
            hashlib.sha256(answer.encode("utf-8")).hexdigest()[:16],
        ]
    )
    return "approved_qa_pair:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def approved_qa_record_to_canonical_row(record: dict, *, chunk_index: int | None = None) -> dict:
    question = _compact(record.get("question"))
    answer = _compact(record.get("approved_answer"))
    normalized_question = _compact(record.get("normalized_question")) or normalize_question_for_exact_match(question)
    normalized_question = normalize_question_for_exact_match(normalized_question)
    citation = _first_citation(record)
    source_doc = _compact(citation.get("source_doc")) or _compact(record.get("source_doc")) or "approved_qa"
    source_pages = _source_pages(citation.get("source_pages") or record.get("source_pages"))
    title = _compact(citation.get("title")) or _compact(record.get("title")) or "Approved Q&A"
    qa_id = _compact(record.get("qa_id"))
    pair_text = f"Q: {question}\nA: {answer}"
    section_path = [item for item in [title, _compact(record.get("question_item"))] if item]
    return {
        "id": _stable_id(record, normalized_question, answer),
        "text": pair_text,
        "source_doc": source_doc,
        "source_pages": source_pages,
        "doc_id": source_doc,
        "chunk_index": chunk_index,
        "searchable": 1,
        "type": "approved_qa",
        "quality": "approved",
        "doc_type": DOC_TYPE,
        "chunk_type": CHUNK_TYPE,
        "title": title,
        "section_path": section_path,
        "chunk_role": "parent",
        "parent_chunk_id": qa_id,
        "searchable_text": pair_text,
        "display_text": pair_text,
        "language": _compact(record.get("language")) or "ja",
        "tenant_id": _compact(record.get("tenant_id")) or "default",
        "doc_version": _compact(record.get("doc_version")),
        "extraction_method": "approved_qa_to_canonical_jsonl",
        "qa_id": qa_id,
        "question_text": question,
        "answer_text": answer,
        "normalized_question": normalized_question,
        "approved_answer": answer,
        "approved_citations": record.get("approved_citations") or [],
        "source_citation_chunk_id": _compact(citation.get("chunk_id")),
        "source_question_no": record.get("source_question_no"),
        "question_item": _compact(record.get("question_item")),
        "reviewed_by": _compact(record.get("reviewed_by")),
        "reviewed_at": _compact(record.get("reviewed_at")),
        "review_notes": _compact(record.get("review_notes")),
    }


def _validate_rows(rows: List[dict]) -> None:
    seen_ids: set[str] = set()
    for idx, row in enumerate(rows, start=1):
        if not _compact(row.get("id")):
            raise ValueError(f"row {idx}: missing id")
        if row["id"] in seen_ids:
            raise ValueError(f"row {idx}: duplicate id: {row['id']}")
        seen_ids.add(row["id"])
        for key in ("text", "searchable_text", "display_text", "qa_id", "question_text", "answer_text"):
            if not _compact(row.get(key)):
                raise ValueError(f"row {idx}: missing {key}")
        pages = row.get("source_pages")
        if not isinstance(pages, list) or not all(isinstance(page, int) for page in pages):
            raise ValueError(f"row {idx}: invalid source_pages")
        if row.get("doc_type") != DOC_TYPE or row.get("chunk_type") != CHUNK_TYPE:
            raise ValueError(f"row {idx}: invalid qa_pair metadata")


def write_jsonl(path: str | Path, rows: Iterable[dict]) -> None:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def convert_approved_qa_to_canonical(
    *,
    input_path: str | Path,
    output_path: str | Path,
) -> dict:
    records = _read_jsonl(input_path)
    validation_errors = validate_approved_qa_records(records)
    if validation_errors:
        raise ValueError("invalid approved QA input: " + "; ".join(validation_errors))

    rows: List[dict] = []
    skipped = 0
    for record in records:
        if _compact(record.get("status")) != "approved":
            skipped += 1
            continue
        rows.append(approved_qa_record_to_canonical_row(record, chunk_index=len(rows) + 1))

    _validate_rows(rows)
    write_jsonl(output_path, rows)
    summary = {
        "input_records": len(records),
        "approved_records": len(rows),
        "written_records": len(rows),
        "skipped_records": skipped,
        "output_path": str(output_path),
    }
    print(
        "Summary: "
        f"input_records={summary['input_records']} "
        f"approved_records={summary['approved_records']} "
        f"written_records={summary['written_records']} "
        f"skipped_records={summary['skipped_records']} "
        f"output_path={summary['output_path']}"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert approved Q&A JSONL to canonical qa_pair JSONL.")
    parser.add_argument("--input", required=True, dest="input_path")
    parser.add_argument("--output", required=True, dest="output_path")
    args = parser.parse_args()
    convert_approved_qa_to_canonical(input_path=args.input_path, output_path=args.output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
