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
- page headers and footers: one "header_footer" chunk with their distinct text.
  It is stored but not searchable (chunk_role="header_footer", searchable=0)
  unless DOCX_HEADER_FOOTER_SEARCHABLE=true; document number, revision,
  effective date and confidentiality found in it become metadata of every
  other chunk of the document (see header_footer_metadata)
- tables nested in a cell: their text joins the cell (stage 9; was dropped)
- images (word/media): OCR text as "image_text" chunks, only with
  OFFICE_OCR_IMAGES=true (stage 9)
- multi-row table headers (2026-11): rows marked "repeat as header row"
  (w:tblHeader), or a first row that groups columns (a horizontal span such
  as 上限額 over 国内 | 海外, or a cell merged down into the next row), form
  the header together; each column is labelled "上限額 国内". A first row
  that is one cell spanning the whole table is the table title.
"""

from __future__ import annotations

import os
import re
import unicodedata
import xml.etree.ElementTree as ET  # element types only; parsing uses defusedxml  # nosec B405
from defusedxml.ElementTree import fromstring as _safe_fromstring
from rag_core.document_converters.limits import MAX_GRID_SPAN, open_office_zip, read_member
import zipfile
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from rag_core.document_converters.table_layout import _mostly_text
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
    return _table_grid(table)[0]


def _table_grid(table: ET.Element) -> Tuple[List[List[str]], List[Dict[str, object]]]:
    """Cell values per row (merges expanded) and per-row layout facts for header detection:
    tbl_header (w:tblHeader), spans [(column, width)], v_restart / v_continue column sets."""
    rows: List[List[str]] = []
    meta: List[Dict[str, object]] = []
    above: Dict[int, str] = {}
    for tr in table.findall(f"{_W}tr"):
        row: List[str] = []
        info: Dict[str, object] = {
            "tbl_header": tr.find(f"{_W}trPr/{_W}tblHeader") is not None
            and _val(tr.find(f"{_W}trPr/{_W}tblHeader")) not in {"0", "false", "off"},
            "spans": [],
            "v_restart": set(),
            "v_continue": set(),
        }
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
                info["v_continue"].add(column)  # type: ignore[union-attr]
            else:
                value = compact(" ".join(_paragraph_text(p) for p in _cell_paragraphs(tc)))
                if v_merge is not None:
                    info["v_restart"].add(column)  # type: ignore[union-attr]
            if span > 1:
                info["spans"].append((column, span))  # type: ignore[union-attr]
            for offset in range(max(1, span)):
                row.append(value)
                above[column + offset] = value
        rows.append(row)
        meta.append(info)
    return rows, meta


_MAX_HEADER_ROWS = 3


def _table_header(rows: List[List[str]], meta: List[Dict[str, object]]) -> Tuple[str, int, List[str]]:
    """(table title, index of the first header row, combined column labels); data starts after the header rows."""
    title = ""
    start = 0
    width = max((len(r) for r in rows), default=0)
    first = [v for v in rows[0] if v] if rows else []
    if len(rows) >= 3 and width >= 2 and len(rows[0]) == width and len(set(rows[0])) == 1 and first:
        title, start = rows[0][0], 1  # one cell across the whole table
    marked = 0
    while start + marked < len(rows) and meta[start + marked]["tbl_header"]:
        marked += 1
    if 0 < marked < len(rows) - start:
        count = min(marked, _MAX_HEADER_ROWS)
    else:
        count = 1
        while count < _MAX_HEADER_ROWS and start + count < len(rows) - 1:
            upper, lower = meta[start + count - 1], meta[start + count]
            groups = any(span < width for _c, span in upper["spans"])  # type: ignore[union-attr]
            merged_down = bool(set(upper["v_restart"]) & set(lower["v_continue"]))  # type: ignore[arg-type]
            if (groups or merged_down) and _mostly_text(rows[start + count]):
                count += 1
            else:
                break
    labels: List[str] = []
    for c in range(width):
        parts: List[str] = []
        for r in range(start, start + count):
            value = rows[r][c] if c < len(rows[r]) else ""
            if value and (not parts or parts[-1] != value):
                parts.append(value)
        labels.append(" ".join(parts))
    return title, start + count, labels


def _header_footer_text(zf: zipfile.ZipFile) -> str:
    seen: List[str] = []
    for name in sorted(n for n in zf.namelist() if _HEADER_FOOTER_RE.match(n)):
        root = _safe_fromstring(read_member(zf, name))
        text = compact(" ".join(_paragraph_text(p) for p in root.iter(f"{_W}p")))
        if text and text not in seen:
            seen.append(text)
    return "\n".join(seen)


_DOC_NUMBER_RE = re.compile(
    r"(?:文書番号|管理番号|文書No\.?|規程番号|Doc(?:ument)?\.?\s*(?:No\.?|Number)|No\.)\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9\-_/]*[A-Za-z0-9])",
    re.IGNORECASE,
)
_REVISION_RE = re.compile(
    r"(?:第\s*(\d+)\s*版|(\d+)\s*版|Rev(?:ision)?\.?\s*[:：]?\s*([A-Za-z]?\d+(?:\.\d+)?)|改訂\s*[:：]?\s*(?:第)?\s*(\d+))",
    re.IGNORECASE,
)
_DATE_PATTERNS = (
    re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"),
    re.compile(r"(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})"),
)
_REIWA_RE = re.compile(r"令和\s*(\d{1,2}|元)\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_CONFIDENTIALITY = (
    ("社外秘", "社外秘"),
    ("部外秘", "部外秘"),
    ("機密", "機密"),
    ("confidential", "Confidential"),
    ("internal", "Internal"),
)


def _iso(year: int, month: int, day: int) -> str:
    if 1 <= month <= 12 and 1 <= day <= 31:
        return f"{year:04d}-{month:02d}-{day:02d}"
    return ""


def header_footer_metadata(text: str) -> Dict[str, str]:
    """Document number / revision / effective date / confidentiality from header+footer text.

    Only keys that were found are returned (all values are strings)."""
    norm = unicodedata.normalize("NFKC", text or "")
    out: Dict[str, str] = {}
    match = _DOC_NUMBER_RE.search(norm)
    if match:
        out["doc_number"] = match.group(1)
    match = _REVISION_RE.search(norm)
    if match:
        out["revision"] = next(g for g in match.groups() if g)
    iso = ""
    match = _REIWA_RE.search(norm)
    if match:
        year = 1 if match.group(1) == "元" else int(match.group(1))
        iso = _iso(2018 + year, int(match.group(2)), int(match.group(3)))
    if not iso:
        for pattern in _DATE_PATTERNS:
            match = pattern.search(norm)
            if match:
                iso = _iso(int(match.group(1)), int(match.group(2)), int(match.group(3)))
                if iso:
                    break
    if iso:
        out["effective_from"] = iso
    lowered = norm.lower()
    for needle, label in _CONFIDENTIALITY:
        if needle.lower() in lowered:
            out["confidentiality"] = label
            break
    return out


def header_footer_searchable() -> bool:
    """DOCX_HEADER_FOOTER_SEARCHABLE (default false): keep the header/footer chunk searchable."""
    return str(os.getenv("DOCX_HEADER_FOOTER_SEARCHABLE", "")).strip().lower() in {"1", "true", "yes", "on"}


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
            cell_rows, row_meta = _table_grid(element)
            if not cell_rows:
                continue
            table_title, data_start, headers = _table_header(cell_rows, row_meta)
            data = [(row_no, cells) for row_no, cells in enumerate(cell_rows[data_start:], start=data_start + 1)]
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
                table_title=table_title,
                extra_builder=lambda row_number, values, _t=table_index, _h=heading_path, _n=data_start: {
                    "table_index": _t,
                    "heading_path": _h,
                    "table_data_start_row": _n + 1,
                },
            )
            for chunk in table_chunks:
                if chunk["chunk_type"] == "row":
                    chunk["chunk_type"] = "table_row"
                chunk["section_path"] = heading_path + ([table_title] if table_title else [])
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
        meta = header_footer_metadata(header_footer)
        if meta:
            for chunk in chunks:
                chunk.update(meta)
        searchable = header_footer_searchable()
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
                extra={
                    "heading_path": [],
                    "chunk_role": "child" if searchable else "header_footer",
                    "searchable": 1 if searchable else 0,
                    **meta,
                },
            )
        )
    return chunks
