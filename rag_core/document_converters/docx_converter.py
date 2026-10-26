"""Minimal stdlib DOCX reader (zipfile + ElementTree).

Walks the document body in order: paragraphs become paragraph chunks under a
heading_path (Heading styles / outline levels), tables become table_row or
qa_pair chunks. No python-docx dependency.

Also handled (v2_08):
- content controls (w:sdt) at body, paragraph and cell level: their text is
  kept (it used to be dropped)
- list numbering: numbered paragraphs keep their number ("1. ", "(2) " ...),
  bullets get "・" (from word/numbering.xml when present)
- footnotes / endnotes: the note text follows the paragraph that cites it
- merged table cells: a horizontal span repeats its value for every grid
  column, a vertical merge repeats the value of the cell above
- page headers and footers: one "header_footer" chunk with their distinct text
- tables nested in a cell: their text joins the cell (stage 9; was dropped)
- images (word/media): OCR text as "image_text" chunks, only with
  OFFICE_OCR_IMAGES=true (stage 9)
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET  # element types only; parsing uses defusedxml  # nosec B405
from defusedxml.ElementTree import fromstring as _safe_fromstring
from rag_core.document_converters.limits import MAX_GRID_SPAN, open_office_zip, read_member
import zipfile
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from rag_core.document_converters.base import (
    build_chunk,
    compact,
    ocr_image_bytes,
    office_ocr_enabled,
    table_rows_to_chunks,
)

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_HEADING_STYLE_RE = re.compile(r"^heading(\d)$", re.IGNORECASE)
_HEADER_FOOTER_RE = re.compile(r"^word/(header|footer)\d*\.xml$")
_NOTE_SEPARATOR_TYPES = {"separator", "continuationSeparator", "continuationNotice"}
_MEDIA_RE = re.compile(r"^word/media/[^/]+\.(png|jpe?g|gif|bmp|tiff?|emf|wmf)$", re.IGNORECASE)


def _val(element: Optional[ET.Element], name: str = "val") -> str:
    return "" if element is None else str(element.get(f"{_W}{name}") or "")


# --- numbering -------------------------------------------------------------------


class _Numbering:
    """numId/ilvl -> format, with running counters per list."""

    def __init__(self, xml: Optional[bytes]):
        self.formats: Dict[Tuple[str, int], Tuple[str, str]] = {}
        self.counters: Dict[Tuple[str, int], int] = {}
        if not xml:
            return
        root = _safe_fromstring(xml)
        abstract: Dict[str, Dict[int, Tuple[str, str]]] = {}
        for node in root.findall(f"{_W}abstractNum"):
            levels = {}
            for lvl in node.findall(f"{_W}lvl"):
                try:
                    ilvl = int(lvl.get(f"{_W}ilvl") or 0)
                except ValueError:
                    continue
                levels[ilvl] = (_val(lvl.find(f"{_W}numFmt")) or "decimal", _val(lvl.find(f"{_W}lvlText")))
            abstract[_val(node, "abstractNumId")] = levels
        for num in root.findall(f"{_W}num"):
            num_id = _val(num, "numId")
            levels = abstract.get(_val(num.find(f"{_W}abstractNumId")), {})
            for ilvl, fmt in levels.items():
                self.formats[(num_id, ilvl)] = fmt

    def prefix(self, paragraph: ET.Element) -> str:
        num_pr = paragraph.find(f"{_W}pPr/{_W}numPr")
        if num_pr is None:
            return ""
        num_id = _val(num_pr.find(f"{_W}numId"))
        try:
            ilvl = int(_val(num_pr.find(f"{_W}ilvl")) or 0)
        except ValueError:
            ilvl = 0
        if not num_id or num_id == "0":
            return ""
        fmt, text = self.formats.get((num_id, ilvl), ("decimal", "%1."))
        if fmt == "bullet":
            return "・"
        key = (num_id, ilvl)
        self.counters[key] = self.counters.get(key, 0) + 1
        # Deeper levels restart when a shallower item of the same list appears.
        for (other_id, other_lvl) in list(self.counters):
            if other_id == num_id and other_lvl > ilvl:
                self.counters.pop((other_id, other_lvl))
        label = text.replace(f"%{ilvl + 1}", str(self.counters[key])) if text else f"{self.counters[key]}."
        label = re.sub(r"%\d", "", label).strip()
        return f"{label} " if label else ""


# --- notes -------------------------------------------------------------------------


def _notes(zf: zipfile.ZipFile, member: str, tag: str) -> Dict[str, str]:
    if member not in zf.namelist():
        return {}
    root = _safe_fromstring(read_member(zf, member))
    out: Dict[str, str] = {}
    for note in root.findall(f"{_W}{tag}"):
        if _val(note, "type") in _NOTE_SEPARATOR_TYPES:
            continue
        text = compact(" ".join(_paragraph_text(p) for p in note.iter(f"{_W}p")))
        if text:
            out[_val(note, "id")] = text
    return out


# --- text ----------------------------------------------------------------------------


def _paragraph_text(p: ET.Element, note_refs: Optional[List[str]] = None) -> str:
    parts: List[str] = []
    for node in p.iter():
        if node.tag == f"{_W}t":
            parts.append(node.text or "")
        elif node.tag in (f"{_W}tab", f"{_W}br", f"{_W}cr"):
            parts.append(" ")
        elif node.tag in (f"{_W}footnoteReference", f"{_W}endnoteReference") and note_refs is not None:
            kind = "f" if node.tag.endswith("footnoteReference") else "e"
            note_id = _val(node, "id")
            note_refs.append(f"{kind}:{note_id}")
            parts.append(f"[注{note_id}]")
    return compact("".join(parts))


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


def _blocks(parent: ET.Element) -> Iterator[ET.Element]:
    """Paragraphs and tables in document order, unwrapping content controls."""
    for child in parent:
        if child.tag in (f"{_W}p", f"{_W}tbl"):
            yield child
        elif child.tag == f"{_W}sdt":
            content = child.find(f"{_W}sdtContent")
            if content is not None:
                yield from _blocks(content)


def _cell_paragraphs(tc: ET.Element) -> Iterator[ET.Element]:
    for block in _blocks(tc):
        if block.tag == f"{_W}p":
            yield block
        elif block.tag == f"{_W}tbl":
            # Nested table: its cells' paragraphs, row by row.
            for tr in block.findall(f"{_W}tr"):
                for inner in tr.findall(f"{_W}tc"):
                    yield from _cell_paragraphs(inner)


def _table_cells(table: ET.Element) -> List[List[str]]:
    rows: List[List[str]] = []
    above: Dict[int, str] = {}
    for tr in table.findall(f"{_W}tr"):
        row: List[str] = []
        cells = [c for c in tr if c.tag in (f"{_W}tc", f"{_W}sdt")]
        flat: List[ET.Element] = []
        for cell in cells:
            if cell.tag == f"{_W}sdt":
                content = cell.find(f"{_W}sdtContent")
                flat.extend(content.findall(f"{_W}tc") if content is not None else [])
            else:
                flat.append(cell)
        for tc in flat:
            tc_pr = tc.find(f"{_W}tcPr")
            try:
                span = int(_val(tc_pr.find(f"{_W}gridSpan")) or 1) if tc_pr is not None else 1
            except ValueError:
                span = 1
            span = min(max(1, span), MAX_GRID_SPAN)  # hostile markup asks for millions of cells
            v_merge = tc_pr.find(f"{_W}vMerge") if tc_pr is not None else None
            column = len(row)
            if v_merge is not None and _val(v_merge) != "restart":
                value = above.get(column, "")
            else:
                value = compact(" ".join(_paragraph_text(p) for p in _cell_paragraphs(tc)))
            for offset in range(max(1, span)):
                row.append(value)
                above[column + offset] = value
        rows.append(row)
    return rows


def _header_footer_text(zf: zipfile.ZipFile) -> str:
    seen: List[str] = []
    for name in sorted(n for n in zf.namelist() if _HEADER_FOOTER_RE.match(n)):
        root = _safe_fromstring(read_member(zf, name))
        text = compact(" ".join(_paragraph_text(p) for p in root.iter(f"{_W}p")))
        if text and text not in seen:
            seen.append(text)
    return "\n".join(seen)


def _image_texts(zf: zipfile.ZipFile) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for name in sorted(n for n in zf.namelist() if _MEDIA_RE.match(n)):
        text = ocr_image_bytes(read_member(zf, name), name)
        if text:
            out.append((name, text))
    return out


def convert_docx(
    path: str | Path,
    *,
    tenant_id: str = "default",
    source_doc: str | None = None,
    doc_type: str | None = None,
) -> List[Dict]:
    path = Path(path)
    source_doc = source_doc or path.name
    with open_office_zip(path) as zf:
        root = _safe_fromstring(read_member(zf, "word/document.xml"))
        numbering = _Numbering(read_member(zf, "word/numbering.xml") if "word/numbering.xml" in zf.namelist() else None)
        footnotes = _notes(zf, "word/footnotes.xml", "footnote")
        endnotes = _notes(zf, "word/endnotes.xml", "endnote")
        header_footer = _header_footer_text(zf)
        image_texts = _image_texts(zf) if office_ocr_enabled() else []
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

    for element in _blocks(body):
        if element.tag == f"{_W}p":
            paragraph_index += 1
            refs: List[str] = []
            text = _paragraph_text(element, refs)
            if not text:
                continue
            level = _heading_level(element)
            if level is not None:
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, text))
                continue
            text = numbering.prefix(element) + text
            for ref in refs:
                kind, note_id = ref.split(":", 1)
                note = (footnotes if kind == "f" else endnotes).get(note_id)
                if note:
                    text += f" 注{note_id}: {note}"
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

    for image_no, (member, image_text) in enumerate(image_texts, start=1):
        chunks.append(
            build_chunk(
                source_type="docx",
                source_doc=source_doc,
                location=f"img{image_no}",
                text=image_text,
                chunk_type="image_text",
                chunk_index=len(chunks) + 1,
                tenant_id=tenant_id,
                doc_type=resolved_doc_type,
                parser="tesseract_ocr",
                title=source_doc,
                extra={"heading_path": [], "image_part": member, "extraction_method": "ocr_image"},
            )
        )

    if header_footer:
        chunks.append(
            build_chunk(
                source_type="docx",
                source_doc=source_doc,
                location="header_footer",
                text=header_footer,
                chunk_type="header_footer",
                chunk_index=len(chunks) + 1,
                tenant_id=tenant_id,
                doc_type=resolved_doc_type,
                parser="stdlib_ooxml",
                title=source_doc,
                extra={"heading_path": []},
            )
        )
    return chunks
