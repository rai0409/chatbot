"""Helpers without third-party dependencies."""

from __future__ import annotations

import os
import re
import unicodedata
from html.parser import HTMLParser
from typing import Any, Dict, List, Sequence

_JA = r"[ぁ-んァ-ヴー一-龥々〆〤]"
_JA_SPACE_RE = re.compile(rf"(?<={_JA})[ 　]+(?={_JA})")
_NUMERIC_RE = re.compile(r"^[\d,.\-−+%/:()（）年月日円万億 ]+$")


def env_str(name: str, default: str = "") -> str:
    return str(os.getenv(name, "") or "").strip() or default


def env_flag(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def env_float(name: str, default: float) -> float:
    try:
        return float(str(os.getenv(name, "") or "").strip() or default)
    except ValueError:
        return default


def env_list(name: str, default: str) -> List[str]:
    return [p.strip().lower() for p in env_str(name, default).split(",") if p.strip()]


def cell_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value if value is not None else ""))
    return re.sub(r"\s+", " ", text).strip()


def normalize_ocr_text(text: str) -> str:
    """NFKC, no spaces between Japanese characters, no empty lines."""
    text = unicodedata.normalize("NFKC", text or "")
    text = _JA_SPACE_RE.sub("", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def is_numeric(value: str) -> bool:
    return bool(value) and bool(_NUMERIC_RE.fullmatch(value))


def clean_grid(rows: Sequence[Sequence[Any]]) -> List[List[str]]:
    """Cells as text, rectangular, without empty rows and empty columns."""
    grid = [[cell_text(c) for c in (row or [])] for row in rows or []]
    grid = [r for r in grid if any(r)]
    if not grid:
        return []
    width = max(len(r) for r in grid)
    grid = [r + [""] * (width - len(r)) for r in grid]
    keep = [i for i in range(width) if any(r[i] for r in grid)]
    return [[r[i] for i in keep] for r in grid]


def fill_merged_header(rows: List[List[str]]) -> List[List[str]]:
    """A header cell spanning columns comes back as "A", "", "": repeat "A"."""
    if not rows:
        return rows
    first = list(rows[0])
    for i in range(1, len(first)):
        if not first[i] and first[i - 1]:
            first[i] = first[i - 1]
    return [first] + rows[1:]


def detect_header_rows(rows: List[List[str]]) -> int:
    """2 when row 0 is a grouped header over a complete, textual row 1."""
    if len(rows) < 3:
        return 1
    top, sub = rows[0], rows[1]
    distinct_top = len({c for c in top if c})
    textual_sub = all(c and not is_numeric(c) for c in sub)
    numeric_below = any(is_numeric(c) for r in rows[2:] for c in r)
    if textual_sub and distinct_top < len(top) and numeric_below:
        return 2
    return 1


def plausible_borderless(rows: List[List[str]]) -> float:
    """0..1 score that a text-aligned block is a table and not prose.

    Prose split into "columns" has long cells, few rows and a low fill
    ratio; real tables have short cells in a regular grid.
    """
    if len(rows) < 3:
        return 0.0
    width = max(len(r) for r in rows)
    if width < 2 or width > 15:
        return 0.0
    cells = [c for r in rows for c in r]
    filled = [c for c in cells if c]
    fill = len(filled) / max(1, len(cells))
    avg_len = sum(len(c) for c in filled) / max(1, len(filled))
    sentences = sum(1 for c in filled if c.endswith(("。", ".")) and len(c) > 25)
    long_cells = sum(1 for c in filled if len(c) > 30)
    if fill < 0.55 or avg_len > 40 or max(len(c) for c in filled) > 80:
        return 0.0
    if sentences and (sentences > len(rows) // 3 or len(rows) < 5) or long_cells > len(filled) // 4:
        return 0.0
    numeric = sum(1 for c in filled if is_numeric(c)) / max(1, len(filled))
    # A complete text-only grid scores 0.65; numbers make a table likelier.
    return round(min(0.85, 0.45 + 0.2 * fill + 0.35 * numeric), 3)


class _HtmlTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: List[List[List[Dict[str, Any]]]] = []
        self._tables: List[List[List[Dict[str, Any]]]] = []  # open tables
        self._cells: List[Dict[str, Any]] = []  # open cells (nested tables)

    @staticmethod
    def _span(attrs: Dict[str, Any], key: str) -> int:
        try:
            return max(1, min(50, int(attrs.get(key) or 1)))
        except ValueError:
            return 1

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table":
            self._tables.append([])
        elif tag == "tr" and self._tables:
            self._tables[-1].append([])
        elif tag in ("td", "th") and self._tables:
            if not self._tables[-1]:
                self._tables[-1].append([])
            cell = {"text": "", "rowspan": self._span(attrs, "rowspan"), "colspan": self._span(attrs, "colspan")}
            self._tables[-1][-1].append(cell)
            self._cells.append(cell)
        elif tag == "br":
            self.handle_data(" ")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cells:
            self._cells.pop()
        elif tag == "table" and self._tables:
            # A nested table is reported as a table of its own; its text also
            # stays in the enclosing cell (handle_data writes to every open cell).
            self.tables.append(self._tables.pop())

    def handle_data(self, data):
        for cell in self._cells:
            cell["text"] += data


def html_tables(html: str) -> List[List[List[str]]]:
    """Every <table> as a grid; rowspan/colspan values are repeated."""
    parser = _HtmlTableParser()
    parser.feed(html or "")
    grids: List[List[List[str]]] = []
    for table in parser.tables:
        grid: Dict[tuple, str] = {}
        for r, row in enumerate(table):
            c = 0
            for cell in row:
                while (r, c) in grid:
                    c += 1
                for dr in range(cell["rowspan"]):
                    for dc in range(cell["colspan"]):
                        grid[(r + dr, c + dc)] = cell_text(cell["text"])
                c += cell["colspan"]
        if not grid:
            continue
        n_rows = max(k[0] for k in grid) + 1
        n_cols = max(k[1] for k in grid) + 1
        grids.append([[grid.get((r, c), "") for c in range(n_cols)] for r in range(n_rows)])
    return grids
