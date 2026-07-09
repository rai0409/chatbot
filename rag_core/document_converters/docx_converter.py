"""Minimal stdlib DOCX reader (zipfile + ElementTree).

Walks the document body in order: paragraphs become paragraph chunks under a
heading_path (Heading styles / outline levels), tables become table_row or
qa_pair chunks. No python-docx dependency.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Dict, List

from rag_core.document_converters.base import build_chunk, compact, table_rows_to_chunks

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_HEADING_STYLE_RE = re.compile(r"^heading(\d)$", re.IGNORECASE)


def _paragraph_text(p: ET.Element) -> str:
    return compact("".join(t.text or "" for t in p.iter(f"{_W}t")))


def _heading_level(p: ET.Element) -> int | None:
    ppr = p.find(f"{_W}pPr")
    if ppr is None:
        return None
    style = ppr.find(f"{_W}pStyle")
    if style is not None:
        match = _HEADING_STYLE_RE.match(style.get(f"{_W}val") or "")
        if match:
            return int(match.group(1))
    outline = ppr.find(f"{_W}outlineLvl")
    if outline is not None:
        try:
            return int(outline.get(f"{_W}val") or "") + 1
        except ValueError:
            return None
    return None


def _table_cells(table: ET.Element) -> List[List[str]]:
    rows = []
    for tr in table.findall(f"{_W}tr"):
        rows.append(
            [
                compact(" ".join(_paragraph_text(p) for p in tc.iter(f"{_W}p")))
                for tc in tr.findall(f"{_W}tc")
            ]
        )
    return rows


def convert_docx(
    path: str | Path,
    *,
    tenant_id: str = "default",
    source_doc: str | None = None,
    doc_type: str | None = None,
) -> List[Dict]:
    path = Path(path)
    source_doc = source_doc or path.name
    with zipfile.ZipFile(path) as zf:
        root = ET.fromstring(zf.read("word/document.xml"))
    body = root.find(f"{_W}body")
    if body is None:
        return []

    chunks: List[Dict] = []
    heading_stack: List[tuple[int, str]] = []
    paragraph_index = 0
    table_index = 0
    resolved_doc_type = doc_type or "document"

    def _heading_path() -> List[str]:
        return [title for _, title in heading_stack]

    for element in body:
        if element.tag == f"{_W}p":
            paragraph_index += 1
            text = _paragraph_text(element)
            if not text:
                continue
            level = _heading_level(element)
            if level is not None:
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, text))
                continue
            chunks.append(
                build_chunk(
                    source_type="docx",
                    source_doc=source_doc,
                    location=f"p{paragraph_index}",
                    text=text,
                    chunk_type="paragraph",
                    chunk_index=len(chunks) + 1,
                    tenant_id=tenant_id,
                    doc_type=resolved_doc_type,
                    parser="stdlib_ooxml",
                    title=heading_stack[-1][1] if heading_stack else source_doc,
                    section_path=_heading_path(),
                    extra={
                        "heading_path": _heading_path(),
                        "paragraph_index": paragraph_index,
                    },
                )
            )
        elif element.tag == f"{_W}tbl":
            table_index += 1
            cell_rows = _table_cells(element)
            if not cell_rows:
                continue
            headers = cell_rows[0]
            data = [(row_no, cells) for row_no, cells in enumerate(cell_rows[1:], start=2)]
            heading_path = _heading_path()
            table_chunks = table_rows_to_chunks(
                headers=headers,
                rows=data,
                source_type="docx",
                source_doc=source_doc,
                location_prefix=f"t{table_index}:",
                tenant_id=tenant_id,
                doc_type=resolved_doc_type,
                parser="stdlib_ooxml",
                start_index=len(chunks) + 1,
                extra_builder=lambda row_number, values, _t=table_index, _h=heading_path: {
                    "table_index": _t,
                    "heading_path": _h,
                },
            )
            for chunk in table_chunks:
                if chunk["chunk_type"] == "row":
                    chunk["chunk_type"] = "table_row"
                chunk["section_path"] = heading_path
            chunks.extend(table_chunks)
    return chunks
