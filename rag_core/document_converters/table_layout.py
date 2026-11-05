"""Where the table of a CSV / Excel sheet really starts.

Exports and reports put a title and a few "output date" lines above the table and a total below it. Taking the
first row as the header (the old behaviour) labels every column with the title ("経費明細: 日付") and loses the real
column names. find_layout() looks at how many cells each row fills:

- the header is the first row that fills (almost) the usual width with mostly non-numeric cells and is followed by
  a data row; rows above it are context: a one-cell row is the title, "label | value" rows are notes
- a row below the header with an empty first cell that names a total (合計, 小計, Total ...) is a summary row:
  it stays in the data, flagged, so it is not mistaken for a record
- anything that does not look like this (plain tables, single-column lists, all-numeric data) keeps the first row
  as the header, exactly as before
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Sequence, Set, Tuple

_NUMBER_RE = re.compile(r"^[\s¥$€\d,.\-+%/:年月日円]+$")
_SUMMARY_RE = re.compile(r"^(合計|総計|小計|計|合計金額|総合計|total|sum|subtotal)$", re.IGNORECASE)


@dataclass
class TableLayout:
    header_index: int = 0  # index into the given rows
    title: str = ""
    notes: List[str] = field(default_factory=list)
    summary_rows: Set[int] = field(default_factory=set)  # row numbers


def _filled(cells: Sequence[str]) -> List[str]:
    return [str(c).strip() for c in cells if str(c or "").strip()]


def _mostly_text(cells: Sequence[str]) -> bool:
    values = _filled(cells)
    return bool(values) and sum(1 for v in values if not _NUMBER_RE.fullmatch(v)) >= 0.6 * len(values)


def find_layout(rows: Sequence[Tuple[int, Sequence[str]]]) -> TableLayout:
    """rows: (row_number, cells) in sheet order (blank rows may be included)."""
    layout = TableLayout()
    counts = [len(_filled(cells)) for _n, cells in rows]
    wide = [c for c in counts if c >= 2]
    if not wide:
        return layout
    width = Counter(wide).most_common(1)[0][0]
    for index, (_n, cells) in enumerate(rows):
        n = counts[index]
        nxt = next((counts[j] for j in range(index + 1, len(rows)) if counts[j] > 0), 0)
        if n >= max(2, int(0.8 * width + 0.999)) and _mostly_text(cells) and nxt >= max(1, width // 2):
            layout.header_index = index
            break
    else:
        return layout
    for _n, cells in rows[: layout.header_index]:
        values = _filled(cells)
        if len(values) == 1 and not layout.title:
            layout.title = values[0]
        elif len(values) >= 2:
            layout.notes.append(": ".join(values[:2]))
        elif len(values) == 1:
            layout.notes.append(values[0])
    header_n = counts[layout.header_index]
    for index in range(layout.header_index + 1, len(rows)):
        number, cells = rows[index]
        values = _filled(cells)
        if values and not str(cells[0] or "").strip() and counts[index] <= max(2, header_n // 2) and any(_SUMMARY_RE.fullmatch(v) for v in values):
            layout.summary_rows.add(number)
    return layout
