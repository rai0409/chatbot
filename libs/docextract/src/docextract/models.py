"""Result types shared by every backend (no third-party imports)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# (x0, y0, x1, y1) in PDF points, origin at the top-left of the page.
BBox = Tuple[float, float, float, float]


@dataclass
class TableRegion:
    page: int  # 1-based
    bbox: Optional[BBox] = None  # None when the backend gives no position


@dataclass
class Table:
    """One logical table; a table continued on the next page has two regions."""

    rows: List[List[str]]
    regions: List[TableRegion]
    backend: str
    # 0..1, how sure the backend is that this is a real table (ruled tables
    # ~0.9, borderless guesses lower). Used to drop doubtful tables.
    confidence: float = 0.9
    header_rows: int = 1

    @property
    def page(self) -> int:
        return self.regions[0].page if self.regions else 0

    @property
    def pages(self) -> List[int]:
        return sorted({r.page for r in self.regions})

    @property
    def n_cols(self) -> int:
        return max((len(r) for r in self.rows), default=0)

    def header(self) -> List[str]:
        """Column names; a two-row header becomes "上位/下位"."""
        if not self.rows:
            return []
        if self.header_rows <= 1 or len(self.rows) < 2:
            return list(self.rows[0])
        top, sub = self.rows[0], self.rows[1]
        names = []
        for i in range(self.n_cols):
            a = top[i] if i < len(top) else ""
            b = sub[i] if i < len(sub) else ""
            names.append(f"{a}/{b}" if a and b and a != b else (a or b))
        return names

    def body(self) -> List[List[str]]:
        return [list(r) for r in self.rows[max(1, self.header_rows):]]

    def to_dict(self) -> dict:
        return {
            "rows": self.rows,
            "regions": [{"page": r.page, "bbox": list(r.bbox) if r.bbox else None} for r in self.regions],
            "backend": self.backend,
            "confidence": self.confidence,
            "header_rows": self.header_rows,
        }


@dataclass
class OcrLine:
    text: str
    confidence: Optional[float] = None  # 0..100
    bbox: Optional[BBox] = None  # pixels of the input image


@dataclass
class OcrResult:
    text: str
    engine: str
    confidence: Optional[float] = None  # mean line confidence, 0..100
    lines: List[OcrLine] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.text.strip())
