"""Minimal stdlib XLSX reader (zipfile + ElementTree).

Reads cached cell values only: shared strings, inline strings, and numbers.
Formulas surface as their cached result. Number formats from xl/styles.xml
are applied so values keep their meaning: date/time serials become ISO
dates, percent cells "15%", currency cells keep their symbol (1900 and 1904
date systems are both handled).

Also handled (2026-11 conversion-accuracy work):
- Japanese era date formats ([$-ja-JP]ggge"年"m"月"d"日", [$-411]ge.m.d ...)
  are rendered the way Excel shows them (令和6年4月1日, R6.4.1) instead of
  as ISO dates
- furigana (<rPh> phonetic runs of shared / inline strings) is not text: it
  used to be glued to the name ("山田太郎ヤマダタロウ")
- a formula cell without a cached value (file written by a tool that never
  calculated it) has no value to show; the row's chunk carries
  conversion_warnings=[{"code": "formula_without_cached_value", ...}] and the
  converter logs it, instead of silently showing an empty cell
"""

from __future__ import annotations

import logging
import os
import re
import xml.etree.ElementTree as ET  # element types only; parsing uses defusedxml  # nosec B405
from defusedxml.ElementTree import fromstring as _safe_fromstring
from rag_core.document_converters.limits import DocumentTooLarge, max_cells, max_columns, open_office_zip, read_member
import zipfile
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List

from rag_core.document_converters.table_layout import find_layout
from rag_core.document_converters.base import table_rows_to_chunks

_NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_NS_REL_ATTR = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_NS_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"
_CELL_REF_RE = re.compile(r"^([A-Z]+)(\d+)$")
_LOG = logging.getLogger(__name__)
FORMULA_WITHOUT_CACHE = "formula_without_cached_value"


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
    root = _safe_fromstring(read_member(zf, name))
    return [_string_item_text(si) for si in root.findall(f"{_NS_MAIN}si")]


def _string_item_text(item: ET.Element) -> str:
    """Text of a shared-string <si> / inline <is>: plain <t> and rich-text runs <r><t>.

    Phonetic runs (<rPh>, furigana) are reading aids, not text, and are left out."""
    parts: List[str] = []
    for child in item:
        if child.tag == f"{_NS_MAIN}t":
            parts.append(child.text or "")
        elif child.tag == f"{_NS_MAIN}r":
            parts.extend(t.text or "" for t in child.findall(f"{_NS_MAIN}t"))
    return "".join(parts)


def _sheet_targets(zf: zipfile.ZipFile) -> List[tuple[str, str]]:
    """Ordered (sheet_name, member_path) pairs from the workbook."""
    rels_root = _safe_fromstring(read_member(zf, "xl/_rels/workbook.xml.rels"))
    targets = {}
    for rel in rels_root.iter(_NS_PKG_REL):
        target = rel.get("Target", "")
        if target.startswith("/"):
            target = target.lstrip("/")
        else:
            target = "xl/" + target
        targets[rel.get("Id")] = target
    workbook_root = _safe_fromstring(read_member(zf, "xl/workbook.xml"))
    sheets = []
    for sheet in workbook_root.iter(f"{_NS_MAIN}sheet"):
        rel_id = sheet.get(_NS_REL_ATTR)
        member = targets.get(rel_id)
        if member and member in zf.namelist():
            # Hidden / very hidden sheets usually hold lookup tables or old
            # data; XLSX_INCLUDE_HIDDEN=true keeps them.
            if (sheet.get("state") or "visible") != "visible" and not _include_hidden():
                continue
            sheets.append((sheet.get("name") or member, member))
    return sheets


def _include_hidden() -> bool:
    return str(os.getenv("XLSX_INCLUDE_HIDDEN", "")).strip().lower() in {"1", "true", "yes", "on"}


def _parse_range(ref: str) -> tuple[int, int, int, int] | None:
    parts = ref.split(":")
    if len(parts) != 2:
        return None
    start, end = _CELL_REF_RE.match(parts[0]), _CELL_REF_RE.match(parts[1])
    if not start or not end:
        return None
    return (
        int(start.group(2)),
        _column_index(start.group(1)),
        int(end.group(2)),
        _column_index(end.group(1)),
    )


# Built-in number format ids (ECMA-376 18.8.30).
_BUILTIN_DATE = {14, 15, 16, 17, 22, 27, 30, 36, 50, 57}
_BUILTIN_TIME = {18, 19, 20, 21, 45, 46, 47}
_BUILTIN_PERCENT = {9, 10}
_QUOTED_RE = re.compile(r'"[^"]*"|\\.')
# Locale/colour blocks such as [$-411] or [Red] are dropped; elapsed-time
# blocks [h] [mm] [ss] are kept because they mark a time format.
_BRACKET_RE = re.compile(r"\[(?![hms]+\])[^\]]*\]")


_JA_LOCALE_RE = re.compile(r"\[\$-(?:411|ja-JP|[0-9A-Fa-f]*0411)\]", re.IGNORECASE)


def _classify_format_code(code: str) -> str:
    raw = code or ""
    plain = _BRACKET_RE.sub("", _QUOTED_RE.sub("", raw)).lower().replace("general", "")
    if "%" in plain:
        return "percent"
    # Japanese era formats: g/gg/ggg (era name) or, with the Japanese locale,
    # e/ee (era year). The kind carries the code so the value is rendered
    # with it (令和6年4月1日) rather than as an ISO date.
    if "g" in plain or ("e" in plain and _JA_LOCALE_RE.search(raw) and ("m" in plain or "d" in plain)):
        if "h" not in plain and "s" not in plain:
            return f"era_date:{raw}"
    # "e" is not treated as a date token: it also appears in scientific
    # formats (0.00E+00). Japanese era formats carry "g" (ggge).
    has_date = "y" in plain or "d" in plain or "g" in plain or ("m" in plain and "h" not in plain and "s" not in plain)
    has_time = "h" in plain or "s" in plain
    if has_date and has_time:
        return "datetime"
    if has_date:
        return "date"
    if has_time:
        return "time"
    for symbol in ("¥", "￥", "$", "€", "円"):
        if symbol in raw:
            return f"currency:{symbol}"
    return ""


def _cell_formats(zf: zipfile.ZipFile) -> List[str]:
    """Format kind per cellXfs index ('' for plain numbers)."""
    name = "xl/styles.xml"
    if name not in zf.namelist():
        return []
    root = _safe_fromstring(read_member(zf, name))
    custom: Dict[int, str] = {}
    num_fmts = root.find(f"{_NS_MAIN}numFmts")
    if num_fmts is not None:
        for fmt in num_fmts.findall(f"{_NS_MAIN}numFmt"):
            try:
                custom[int(fmt.get("numFmtId") or -1)] = fmt.get("formatCode") or ""
            except ValueError:
                continue
    kinds: List[str] = []
    xfs = root.find(f"{_NS_MAIN}cellXfs")
    for xf in (xfs.findall(f"{_NS_MAIN}xf") if xfs is not None else []):
        try:
            fmt_id = int(xf.get("numFmtId") or 0)
        except ValueError:
            fmt_id = 0
        if fmt_id in custom:
            kinds.append(_classify_format_code(custom[fmt_id]))
        elif fmt_id in _BUILTIN_DATE:
            kinds.append("datetime" if fmt_id == 22 else "date")
        elif fmt_id in _BUILTIN_TIME:
            kinds.append("time")
        elif fmt_id in _BUILTIN_PERCENT:
            kinds.append("percent")
        else:
            kinds.append("")
    return kinds


def _uses_1904_dates(zf: zipfile.ZipFile) -> bool:
    try:
        root = _safe_fromstring(read_member(zf, "xl/workbook.xml"))
    except KeyError:
        return False
    pr = root.find(f"{_NS_MAIN}workbookPr")
    return pr is not None and str(pr.get("date1904") or "").lower() in {"1", "true"}


def _trim_decimal(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


# (era name, one-character name, letter, first day). Excel serials start in 1900 (明治33年).
_ERAS = (
    ("令和", "令", "R", date(2019, 5, 1)),
    ("平成", "平", "H", date(1989, 1, 8)),
    ("昭和", "昭", "S", date(1926, 12, 25)),
    ("大正", "大", "T", date(1912, 7, 30)),
    ("明治", "明", "M", date(1868, 1, 25)),
)
_WEEKDAYS_JA = "月火水木金土日"
_ERA_TOKEN_RE = re.compile(r'"([^"]*)"|\\(.)|\[[^\]]*\]|(ggg|gg|g|ee|e|yyyy|yy|mmmm|mmm|mm|m|dddd|ddd|dd|d|aaaa|aaa)|(.)', re.IGNORECASE)


def format_era_date(day: date, code: str) -> str:
    """Render a date with an Excel Japanese-era format code (first section only)."""
    section = code.split(";", 1)[0]
    era = next(((name, short, letter, start) for name, short, letter, start in _ERAS if day >= start), None)
    if era is None:
        return day.isoformat()
    name, short, letter, start = era
    era_year = day.year - start.year + 1
    out: List[str] = []
    for match in _ERA_TOKEN_RE.finditer(section):
        quoted, escaped, token, literal = match.groups()
        if quoted is not None:
            out.append(quoted)
        elif escaped is not None:
            out.append(escaped)
        elif token is not None:
            t = token.lower()
            out.append({
                "ggg": name, "gg": short, "g": letter,
                "ee": f"{era_year:02d}", "e": str(era_year),
                "yyyy": f"{day.year:04d}", "yy": f"{day.year % 100:02d}",
                "mmmm": str(day.month), "mmm": str(day.month), "mm": f"{day.month:02d}", "m": str(day.month),
                "dddd": str(day.day), "ddd": str(day.day), "dd": f"{day.day:02d}", "d": str(day.day),
                "aaaa": _WEEKDAYS_JA[day.weekday()] + "曜日", "aaa": _WEEKDAYS_JA[day.weekday()],
            }[t])
        elif literal is not None and literal not in "_*":
            out.append(literal)
        # [...] blocks (locale, colour) print nothing
    return "".join(out).strip()


def format_number(raw: str, kind: str, *, date1904: bool = False) -> str:
    if not kind or not raw:
        return raw
    try:
        number = Decimal(raw)
    except InvalidOperation:
        return raw
    if kind.startswith("era_date:"):
        base = datetime(1904, 1, 1) if date1904 else datetime(1899, 12, 30)
        try:
            return format_era_date((base + timedelta(days=float(number))).date(), kind.split(":", 1)[1])
        except (OverflowError, ValueError):
            return raw
    if kind in {"date", "datetime", "time"}:
        base = datetime(1904, 1, 1) if date1904 else datetime(1899, 12, 30)
        moment = base + timedelta(days=float(number))
        moment = moment.replace(microsecond=0) + timedelta(seconds=round(moment.microsecond / 1e6))
        if kind == "date" or (kind == "datetime" and number == number.to_integral_value()):
            return moment.date().isoformat()
        if kind == "time":
            return moment.time().isoformat()
        return moment.isoformat(sep=" ")
    if kind == "percent":
        return f"{_trim_decimal(number * 100)}%"
    if kind.startswith("currency:"):
        symbol = kind.split(":", 1)[1]
        amount = _trim_decimal(number)
        return f"{amount}{symbol}" if symbol == "円" else f"{symbol}{amount}"
    return raw


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
        return _string_item_text(is_el)
    v = cell.find(f"{_NS_MAIN}v")
    return (v.text or "") if v is not None else ""


def _formatted_cell_value(cell: ET.Element, shared: List[str], formats: List[str], date1904: bool) -> str:
    value = _cell_value(cell, shared)
    if cell.get("t", "n") not in {"n", ""} or not formats:
        return value
    try:
        style = int(cell.get("s") or 0)
    except ValueError:
        return value
    kind = formats[style] if 0 <= style < len(formats) else ""
    return format_number(value, kind, date1904=date1904)


def _formula_without_cache(cell: ET.Element) -> bool:
    if cell.find(f"{_NS_MAIN}f") is None:
        return False
    v = cell.find(f"{_NS_MAIN}v")
    return v is None or not (v.text or "").strip()


def _sheet_rows(
    zf: zipfile.ZipFile,
    member: str,
    shared: List[str],
    formats: List[str] | None = None,
    date1904: bool = False,
    warnings: List[Dict[str, str]] | None = None,
) -> Dict[int, Dict[int, str]]:
    """warnings: when given, collects {"code", "cell", "row"} for formula cells without a cached value."""
    root = _safe_fromstring(read_member(zf, member))
    rows: Dict[int, Dict[int, str]] = {}
    for row in root.iter(f"{_NS_MAIN}row"):
        row_number = int(row.get("r") or len(rows) + 1)
        if str(row.get("hidden") or "") in {"1", "true"} and not _include_hidden():
            continue
        cells: Dict[int, str] = {}
        for cell in row.findall(f"{_NS_MAIN}c"):
            ref = cell.get("r") or ""
            match = _CELL_REF_RE.match(ref)
            col = _column_index(match.group(1)) if match else len(cells)
            cells[col] = _formatted_cell_value(cell, shared, formats or [], date1904)
            if warnings is not None and _formula_without_cache(cell):
                warnings.append({"code": FORMULA_WITHOUT_CACHE, "cell": ref or f"{_column_letters(col)}{row_number}", "row": row_number})
        if cells:
            rows[row_number] = cells
    # Merged cells: Excel stores the value only in the top-left cell; every
    # cell of the merged range gets it so rows below a merged label keep it.
    # Ranges are clipped to the cells that hold data: "A1:XFD1048576" would
    # otherwise loop over 1.7e10 cells.
    last_row = max(rows) if rows else 0
    last_col = max((max(cells) for cells in rows.values() if cells), default=0)
    for merge in root.iter(f"{_NS_MAIN}mergeCell"):
        bounds = _parse_range(merge.get("ref") or "")
        if bounds is None:
            continue
        r0, c0, r1, c1 = bounds
        r1, c1 = min(r1, last_row), min(c1, last_col)
        value = rows.get(r0, {}).get(c0, "")
        if not value:
            continue
        for r in range(r0, r1 + 1):
            if r not in rows and r != r0:
                rows[r] = {}
            for c in range(c0, c1 + 1):
                if r in rows and not rows[r].get(c):
                    rows[r][c] = value
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
    with open_office_zip(path) as zf:
        shared = _shared_strings(zf)
        formats = _cell_formats(zf)
        date1904 = _uses_1904_dates(zf)
        for sheet_name, member in _sheet_targets(zf):
            sheet_warnings: List[Dict[str, str]] = []
            rows = _sheet_rows(zf, member, shared, formats, date1904, warnings=sheet_warnings)
            if sheet_warnings:
                _LOG.warning(
                    "xlsx %s / sheet %s: %d formula cell(s) without a cached value (shown empty): %s",
                    source_doc, sheet_name[:40], len(sheet_warnings), ", ".join(w["cell"] for w in sheet_warnings[:10]),
                )
            row_warnings: Dict[int, List[Dict[str, str]]] = {}
            for warning in sheet_warnings:
                row_warnings.setdefault(int(warning["row"]), []).append(
                    {"code": warning["code"], "sheet": sheet_name, "cell": warning["cell"]}
                )
            if not rows:
                continue
            ordered = sorted(rows.items())
            width = max(max(r.keys()) for _, r in ordered) + 1
            if width > max_columns() or width * len(ordered) > max_cells():
                raise DocumentTooLarge(f"sheet {sheet_name[:40]}: {len(ordered)} rows x {width} columns")
            grid = [(row_number, [cells.get(i, "") for i in range(width)]) for row_number, cells in ordered]
            layout = find_layout(grid)  # a title above the table is not the header
            headers = grid[layout.header_index][1]
            data = grid[layout.header_index + 1:]
            last_col = _column_letters(width - 1)

            def _extra(row_number: int, values, _sheet=sheet_name, _last=last_col, _warnings=row_warnings):
                extra = {
                    "sheet_name": _sheet,
                    "cell_range": f"A{row_number}:{_last}{row_number}",
                }
                if row_number in _warnings:
                    extra["conversion_warnings"] = _warnings[row_number]
                return extra

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
                    table_title=layout.title,
                    summary_rows=layout.summary_rows,
                    extra_builder=_extra,
                )
            )
    return chunks
