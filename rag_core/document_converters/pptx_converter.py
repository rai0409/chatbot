"""Minimal stdlib PPTX reader (zipfile + ElementTree).

Emits one slide chunk per slide (title + body text) and table_row chunks for
slide tables. No python-pptx dependency. Text inside slide images is added as
"画像内テキスト: ..." only with OFFICE_OCR_IMAGES=true (stage 9).

Also handled (v2_08):
- slide order follows the presentation (ppt/presentation.xml sldIdLst), not
  the file names; slide_number is the position shown to the audience
- speaker notes (notesSlide) are appended to the slide text as "ノート: ..."
- hidden slides (show="0") are skipped unless PPTX_INCLUDE_HIDDEN_SLIDES=true
"""

from __future__ import annotations

import os
import posixpath
import re
import xml.etree.ElementTree as ET  # element types only; parsing uses defusedxml  # nosec B405
from defusedxml.ElementTree import fromstring as _safe_fromstring
from rag_core.document_converters.limits import open_office_zip, read_member
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

from rag_core.document_converters.base import (
    build_chunk,
    compact,
    ocr_image_bytes,
    office_ocr_enabled,
    table_rows_to_chunks,
)

_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"
_SLIDE_RE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
_TITLE_PH_TYPES = {"title", "ctrTitle"}
_NOTES_BODY_PH_TYPES = {"body"}


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


def _rels(zf: zipfile.ZipFile, part: str) -> Dict[str, Dict[str, str]]:
    directory, name = posixpath.split(part)
    rels_name = posixpath.join(directory, "_rels", name + ".rels")
    if rels_name not in zf.namelist():
        return {}
    out: Dict[str, Dict[str, str]] = {}
    for rel in _safe_fromstring(read_member(zf, rels_name)).iter(_PKG_REL):
        target = rel.get("Target") or ""
        resolved = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(directory, target))
        out[rel.get("Id") or ""] = {"target": resolved, "type": rel.get("Type") or ""}
    return out


def _slide_order(zf: zipfile.ZipFile) -> List[str]:
    names = zf.namelist()
    by_file = [m.group(0) for m in sorted((_SLIDE_RE.match(n) for n in names if _SLIDE_RE.match(n)), key=lambda m: int(m.group(1)))]
    if "ppt/presentation.xml" not in names:
        return by_file
    rels = _rels(zf, "ppt/presentation.xml")
    ordered: List[str] = []
    for sld in _safe_fromstring(read_member(zf, "ppt/presentation.xml")).iter(f"{_P}sldId"):
        target = rels.get(sld.get(f"{_R}id") or "", {}).get("target")
        if target and target in names:
            ordered.append(target)
    return ordered or by_file


def _notes_text(zf: zipfile.ZipFile, slide_part: str) -> str:
    for rel in _rels(zf, slide_part).values():
        if rel["type"].endswith("/notesSlide") and rel["target"] in zf.namelist():
            root = _safe_fromstring(read_member(zf, rel["target"]))
            lines: List[str] = []
            for shape in root.iter(f"{_P}sp"):
                ph_types = {ph.get("type") or "" for ph in shape.iter(f"{_P}ph")}
                if ph_types & _NOTES_BODY_PH_TYPES:
                    lines.extend(_shape_paragraph_lines(shape))
            return compact(" ".join(lines))
    return ""


def _image_text(zf: zipfile.ZipFile, slide_part: str) -> List[str]:
    lines: List[str] = []
    for rel in _rels(zf, slide_part).values():
        if rel["type"].endswith("/image") and rel["target"] in zf.namelist():
            text = ocr_image_bytes(read_member(zf, rel["target"]), rel["target"])
            if text:
                lines.append(compact(text))
    return lines


def _include_hidden() -> bool:
    return str(os.getenv("PPTX_INCLUDE_HIDDEN_SLIDES", "")).strip().lower() in {"1", "true", "yes", "on"}


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

    with open_office_zip(path) as zf:
        for slide_number, member in enumerate(_slide_order(zf), start=1):
            root = _safe_fromstring(read_member(zf, member))
            if root.get("show") == "0" and not _include_hidden():
                continue
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
            notes = _notes_text(zf, member)
            image_lines = _image_text(zf, member) if office_ocr_enabled() else []

            slide_title: Optional[str] = compact(" ".join(title_lines)) or None
            slide_text_lines = ([slide_title] if slide_title else []) + body_lines
            if notes:
                slide_text_lines.append(f"ノート: {notes}")
            for image_line in image_lines:
                slide_text_lines.append(f"画像内テキスト: {image_line}")
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
                            "slide_part": member,
                            "has_notes": bool(notes),
                            "has_image_text": bool(image_lines),
                        },
                    )
                )

            for table_no, table in enumerate(tables, start=1):
                cell_rows = []
                for tr in table.findall(f"{_A}tr"):
                    row: List[str] = []
                    for tc in tr.findall(f"{_A}tc"):
                        # Merged cells: hMerge/vMerge continuation cells repeat
                        # the value to their left / above.
                        if tc.get("hMerge") == "1" and row:
                            row.append(row[-1])
                        elif tc.get("vMerge") == "1" and cell_rows and len(cell_rows[-1]) > len(row):
                            row.append(cell_rows[-1][len(row)])
                        else:
                            row.append(compact(" ".join(_shape_paragraph_lines(tc))))
                    cell_rows.append(row)
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
