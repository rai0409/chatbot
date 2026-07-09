"""Shared canonical-chunk building blocks for the multi-format converters.

Every converter emits rows compatible with the existing canonical JSONL
contract (scripts/check_chunks_canonical.py requires id/text/type/source_doc;
scripts/ingest_canonical_jsonl.py defaults tenant_id/source_pages/searchable).
Chunks are self-contained retrieval units (chunk_role="child", no parent),
matching the qa_pair chunk decision from Prompt015.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any, Dict, List, Sequence


# Q/A header vocabularies for FAQ-style tables (CSV/XLSX/DOCX tables).
_QUESTION_HEADERS = {"question", "q", "質問", "問い合わせ", "質疑", "設問"}
_ANSWER_HEADERS = {"answer", "a", "回答", "応答"}

_INT_FLOAT_RE = re.compile(r"^-?\d+\.0$")


def compact(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()


def normalize_header(value: Any) -> str:
    return unicodedata.normalize("NFKC", compact(value)).lower()


def trim_numeric(value: str) -> str:
    # Spreadsheet integers often surface as "5.0"; keep them as "5".
    if _INT_FLOAT_RE.match(value):
        return value[:-2]
    return value


def detect_qa_columns(headers: Sequence[str]) -> tuple[int, int] | None:
    """Return (question_index, answer_index) when a Q/A table is obvious."""
    normalized = [normalize_header(h) for h in headers]
    q_idx = next((i for i, h in enumerate(normalized) if h in _QUESTION_HEADERS), None)
    a_idx = next((i for i, h in enumerate(normalized) if h in _ANSWER_HEADERS), None)
    if q_idx is None or a_idx is None or q_idx == a_idx:
        return None
    return q_idx, a_idx


def stable_chunk_id(source_type: str, source_doc: str, location: str, text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]
    return f"{source_type}:{source_doc}:{location}:h{digest}"


def build_chunk(
    *,
    source_type: str,
    source_doc: str,
    location: str,
    text: str,
    chunk_type: str,
    chunk_index: int,
    tenant_id: str,
    doc_type: str,
    parser: str,
    searchable_text: str | None = None,
    title: str | None = None,
    section_path: Sequence[str] | None = None,
    extra: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    text = text.strip()
    row: Dict[str, Any] = {
        "id": stable_chunk_id(source_type, source_doc, location, text),
        "text": text,
        "source_doc": source_doc,
        "source_pages": [-1],
        "doc_id": source_doc,
        "chunk_index": chunk_index,
        "searchable": 1,
        "type": source_type,
        "quality": "standard",
        "doc_type": doc_type,
        "chunk_type": chunk_type,
        "source_type": source_type,
        "parser": parser,
        "title": title or source_doc,
        "section_path": list(section_path or []),
        # Self-contained retrieval unit (see Prompt015): ranked like ordinary
        # child chunks, never parent-expanded.
        "chunk_role": "child",
        "parent_chunk_id": "",
        "searchable_text": (searchable_text or text).strip(),
        "display_text": text,
        "language": "ja",
        "tenant_id": compact(tenant_id) or "default",
    }
    if extra:
        row.update(extra)
    return row


def table_rows_to_chunks(
    *,
    headers: Sequence[str],
    rows: Sequence[tuple[int, Sequence[str]]],
    source_type: str,
    source_doc: str,
    location_prefix: str,
    tenant_id: str,
    doc_type: str,
    parser: str,
    start_index: int,
    extra_builder=None,
) -> List[Dict[str, Any]]:
    """Convert header + (row_number, cells) pairs into row/qa_pair chunks."""
    headers = [compact(h) for h in headers]
    qa = detect_qa_columns(headers)
    chunks: List[Dict[str, Any]] = []
    index = start_index
    for row_number, cells in rows:
        values = [trim_numeric(compact(c)) for c in cells]
        if not any(values):
            continue
        pairs = [
            (headers[i] if i < len(headers) and headers[i] else f"col{i + 1}", v)
            for i, v in enumerate(values)
            if v
        ]
        extra = {
            "row_number": row_number,
            "columns": [h for h, _ in pairs],
        }
        if extra_builder:
            extra.update(extra_builder(row_number, values))
        location = f"{location_prefix}r{row_number}"
        if qa is not None:
            q_idx, a_idx = qa
            question = values[q_idx] if q_idx < len(values) else ""
            answer = values[a_idx] if a_idx < len(values) else ""
            if question and answer:
                others = [
                    f"{headers[j] if j < len(headers) and headers[j] else f'col{j + 1}'}: {v}"
                    for j, v in enumerate(values)
                    if v and j not in (q_idx, a_idx)
                ]
                text = f"Q: {question}\nA: {answer}"
                searchable = "\n".join([text, *others])
                chunks.append(
                    build_chunk(
                        source_type=source_type,
                        source_doc=source_doc,
                        location=location,
                        text=text,
                        searchable_text=searchable,
                        chunk_type="qa_pair",
                        chunk_index=index,
                        tenant_id=tenant_id,
                        doc_type=doc_type,
                        parser=parser,
                        extra=extra,
                    )
                )
                index += 1
                continue
        text = "\n".join(f"{h}: {v}" for h, v in pairs)
        chunks.append(
            build_chunk(
                source_type=source_type,
                source_doc=source_doc,
                location=location,
                text=text,
                chunk_type="row",
                chunk_index=index,
                tenant_id=tenant_id,
                doc_type=doc_type,
                parser=parser,
                extra=extra,
            )
        )
        index += 1
    return chunks
