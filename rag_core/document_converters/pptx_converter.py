"""Minimal stdlib PPTX reader (zipfile + ElementTree).

Emits one slide chunk per slide (title + body text) and table_row chunks for
slide tables. No image OCR. No python-pptx dependency.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Dict, List

from rag_core.document_converters.base import build_chunk, compact, table_rows_to_chunks

_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_SLIDE_RE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
_TITLE_PH_TYPES = {"title", "ctrTitle"}


def _shape_paragraph_lines(shape: ET.Element) -> List[str]:
    lines = []
    for paragraph in shape.iter(f"{_A}p"):
        line = compact("".join(t.text or "" for t in paragraph.iter(f"{_A}t")))
        if line:
            lines.append(line)
    return lines


def _is_title_shape(shape: ET.Element) -> bool:
    for ph in shape.iter(f"{_P}ph"):
        if (ph.get("type") or "") in _TITLE_PH_TYPES:
            return True
    return False


def convert_pptx(
    path: str | Path,
    *,
    tenant_id: str = "default",
    source_doc: str | None = None,
    doc_type: str | None = None,
) -> List[Dict]:
    path = Path(path)
    source_doc = source_doc or path.name
    resolved_doc_type = doc_type or "presentation"
    chunks: List[Dict] = []

    with zipfile.ZipFile(path) as zf:
        slide_members = sorted(
            (int(m.group(1)), m.group(0))
            for m in (_SLIDE_RE.match(name) for name in zf.namelist())
            if m
        )
        for slide_number, member in slide_members:
            root = ET.fromstring(zf.read(member))
            title_lines: List[str] = []
            body_lines: List[str] = []
            tables: List[ET.Element] = []
            for shape in root.iter(f"{_P}sp"):
                lines = _shape_paragraph_lines(shape)
                if not lines:
                    continue
                if _is_title_shape(shape):
                    title_lines.extend(lines)
                else:
                    body_lines.extend(lines)
            tables.extend(root.iter(f"{_A}tbl"))

            slide_title = compact(" ".join(title_lines)) or None
            slide_text_lines = ([slide_title] if slide_title else []) + body_lines
            if slide_text_lines:
                text = "\n".join(slide_text_lines)
                chunks.append(
                    build_chunk(
                        source_type="pptx",
                        source_doc=source_doc,
                        location=f"s{slide_number}",
                        text=text,
                        chunk_type="slide",
                        chunk_index=len(chunks) + 1,
                        tenant_id=tenant_id,
                        doc_type=resolved_doc_type,
                        parser="stdlib_ooxml",
                        title=slide_title or source_doc,
                        extra={
                            "slide_number": slide_number,
                            "slide_title": slide_title,
                        },
                    )
                )

            for table_no, table in enumerate(tables, start=1):
                cell_rows = []
                for tr in table.findall(f"{_A}tr"):
                    cell_rows.append(
                        [
                            compact(" ".join(_shape_paragraph_lines(tc)))
                            for tc in tr.findall(f"{_A}tc")
                        ]
                    )
                if not cell_rows:
                    continue
                headers = cell_rows[0]
                data = [(row_no, cells) for row_no, cells in enumerate(cell_rows[1:], start=2)]
                table_chunks = table_rows_to_chunks(
                    headers=headers,
                    rows=data,
                    source_type="pptx",
                    source_doc=source_doc,
                    location_prefix=f"s{slide_number}:t{table_no}:",
                    tenant_id=tenant_id,
                    doc_type=resolved_doc_type,
                    parser="stdlib_ooxml",
                    start_index=len(chunks) + 1,
                    extra_builder=lambda row_number, values, _s=slide_number, _t=slide_title: {
                        "slide_number": _s,
                        "slide_title": _t,
                    },
                )
                for chunk in table_chunks:
                    if chunk["chunk_type"] == "row":
                        chunk["chunk_type"] = "table_row"
                chunks.extend(table_chunks)
    return chunks
