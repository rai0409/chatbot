"""Minimal stdlib XLSX reader (zipfile + ElementTree).

Reads cached cell values only: shared strings, inline strings, and numbers.
Formulas surface as their cached result; date cells surface as raw serial
numbers (documented limitation — no openpyxl dependency).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Dict, List

from rag_core.document_converters.base import table_rows_to_chunks

_NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_NS_REL_ATTR = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_NS_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"
_CELL_REF_RE = re.compile(r"^([A-Z]+)(\d+)$")


def _column_index(letters: str) -> int:
    value = 0
    for ch in letters:
        value = value * 26 + (ord(ch) - ord("A") + 1)
    return value - 1


def _column_letters(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


def _shared_strings(zf: zipfile.ZipFile) -> List[str]:
    name = "xl/sharedStrings.xml"
    if name not in zf.namelist():
        return []
    root = ET.fromstring(zf.read(name))
    strings = []
    for si in root.findall(f"{_NS_MAIN}si"):
        strings.append("".join(t.text or "" for t in si.iter(f"{_NS_MAIN}t")))
    return strings


def _sheet_targets(zf: zipfile.ZipFile) -> List[tuple[str, str]]:
    """Ordered (sheet_name, member_path) pairs from the workbook."""
    rels_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    targets = {}
    for rel in rels_root.iter(_NS_PKG_REL):
        target = rel.get("Target", "")
        if target.startswith("/"):
            target = target.lstrip("/")
        else:
            target = "xl/" + target
        targets[rel.get("Id")] = target
    workbook_root = ET.fromstring(zf.read("xl/workbook.xml"))
    sheets = []
    for sheet in workbook_root.iter(f"{_NS_MAIN}sheet"):
        rel_id = sheet.get(_NS_REL_ATTR)
        member = targets.get(rel_id)
        if member and member in zf.namelist():
            sheets.append((sheet.get("name") or member, member))
    return sheets


def _cell_value(cell: ET.Element, shared: List[str]) -> str:
    cell_type = cell.get("t", "n")
    if cell_type == "s":
        v = cell.find(f"{_NS_MAIN}v")
        try:
            return shared[int((v.text or "").strip())] if v is not None else ""
        except (ValueError, IndexError):
            return ""
    if cell_type == "inlineStr":
        is_el = cell.find(f"{_NS_MAIN}is")
        if is_el is None:
            return ""
        return "".join(t.text or "" for t in is_el.iter(f"{_NS_MAIN}t"))
    v = cell.find(f"{_NS_MAIN}v")
    return (v.text or "") if v is not None else ""


def _sheet_rows(zf: zipfile.ZipFile, member: str, shared: List[str]) -> Dict[int, Dict[int, str]]:
    root = ET.fromstring(zf.read(member))
    rows: Dict[int, Dict[int, str]] = {}
    for row in root.iter(f"{_NS_MAIN}row"):
        row_number = int(row.get("r") or len(rows) + 1)
        cells: Dict[int, str] = {}
        for cell in row.findall(f"{_NS_MAIN}c"):
            ref = cell.get("r") or ""
            match = _CELL_REF_RE.match(ref)
            col = _column_index(match.group(1)) if match else len(cells)
            cells[col] = _cell_value(cell, shared)
        if cells:
            rows[row_number] = cells
    return rows


def convert_xlsx(
    path: str | Path,
    *,
    tenant_id: str = "default",
    source_doc: str | None = None,
    doc_type: str | None = None,
) -> List[Dict]:
    path = Path(path)
    source_doc = source_doc or path.name
    chunks: List[Dict] = []
    with zipfile.ZipFile(path) as zf:
        shared = _shared_strings(zf)
        for sheet_name, member in _sheet_targets(zf):
            rows = _sheet_rows(zf, member, shared)
            if not rows:
                continue
            ordered = sorted(rows.items())
            header_row_number, header_cells = ordered[0]
            width = max(max(r.keys()) for _, r in ordered) + 1
            headers = [header_cells.get(i, "") for i in range(width)]
            data = [
                (row_number, [cells.get(i, "") for i in range(width)])
                for row_number, cells in ordered[1:]
            ]
            last_col = _column_letters(width - 1)

            def _extra(row_number: int, values, _sheet=sheet_name, _last=last_col):
                return {
                    "sheet_name": _sheet,
                    "cell_range": f"A{row_number}:{_last}{row_number}",
                }

            chunks.extend(
                table_rows_to_chunks(
                    headers=headers,
                    rows=data,
                    source_type="xlsx",
                    source_doc=source_doc,
                    location_prefix=f"{sheet_name}:",
                    tenant_id=tenant_id,
                    doc_type=doc_type or "table",
                    parser="stdlib_ooxml",
                    start_index=len(chunks) + 1,
                    extra_builder=_extra,
                )
            )
    return chunks
