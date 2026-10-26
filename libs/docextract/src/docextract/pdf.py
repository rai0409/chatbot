"""PDF pages without PyMuPDF.

pdfplumber (MIT, on pdfminer.six) gives text, word boxes, ruling lines,
image positions and tables; page images for OCR come from pypdfium2
(Apache-2.0 / BSD-3-Clause), which pdfplumber already depends on. Both are
permissively licensed, so they can ship inside a commercial product.

    from docextract.pdf import open_pdf
    with open_pdf("manual.pdf") as doc:
        for page in doc.pages:
            text = page.text(exclude=[table_bbox])   # text outside the tables
            image = page.render(dpi=300)              # PIL image for OCR

Japanese specifics:
- spaces that pdfminer inserts between Japanese characters (justified
  lines, wide tracking) are removed; spaces next to Latin text stay
- vertical writing (縦書き) is detected from the character positions and read
  top-to-bottom, right-to-left (pdfplumber char_dir/line_dir)
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Iterator, List, Optional, Sequence, Tuple

from docextract._util import env_float
from docextract.models import BBox

_JA = r"[ぁ-んァ-ヴー一-龥々〆〤、。「」『』（）・]"
_JA_GAP_RE = re.compile(rf"(?<={_JA}) +(?={_JA})")


class PdfUnavailable(RuntimeError):
    pass


class PdfTooLarge(ValueError):
    """More pages than DOCEXTRACT_MAX_PAGES (untrusted uploads)."""


# Rendering (OCR) is scaled down above this many pixels, like images.py.
_MAX_RENDER_PIXELS = 60_000_000


def _inside(obj, boxes: Sequence[BBox]) -> bool:
    cx = (float(obj["x0"]) + float(obj["x1"])) / 2
    cy = (float(obj["top"]) + float(obj["bottom"])) / 2
    return any(x0 <= cx <= x1 and y0 <= cy <= y1 for x0, y0, x1, y1 in boxes)


def join_japanese(text: str) -> str:
    return _JA_GAP_RE.sub("", text or "")


class PdfPage:
    """One page; coordinates are PDF points with the origin at the top-left."""

    def __init__(self, page, number: int):
        self._page = page
        self.number = number
        self.width = float(page.width)
        self.height = float(page.height)

    @property
    def raw(self):
        """The pdfplumber page (for callers that need more)."""
        return self._page

    def vertical_ratio(self) -> float:
        """Share of consecutive characters stacked top-to-bottom (縦書き)."""
        chars = [c for c in self._page.chars if str(c.get("text") or "").strip()]
        if len(chars) < 8:
            return 0.0
        stacked = 0
        for a, b in zip(chars, chars[1:]):
            size = max(float(a.get("size") or 0) or (float(a["bottom"]) - float(a["top"])), 1.0)
            dx = abs(float(b["x0"]) - float(a["x0"]))
            dy = float(b["top"]) - float(a["top"])
            if dx < 0.3 * size and 0 < dy < 2.0 * size:
                stacked += 1
        return stacked / (len(chars) - 1)

    def is_vertical(self) -> bool:
        return self.vertical_ratio() >= env_float("DOCEXTRACT_VERTICAL_RATIO", 0.5)

    def text(self, exclude: Sequence[BBox] = ()) -> str:
        """Page text in reading order, without characters inside `exclude`."""
        page = self._page
        boxes = [tuple(map(float, b)) for b in exclude if b]
        if boxes:
            page = page.filter(lambda obj: not (obj.get("object_type") == "char" and _inside(obj, boxes)))
        kwargs = {"x_tolerance_ratio": env_float("DOCEXTRACT_X_TOLERANCE_RATIO", 0.25), "y_tolerance": 3}
        if self.is_vertical():
            kwargs.update({"char_dir": "ttb", "line_dir": "rtl"})
        try:
            raw = page.extract_text(**kwargs) or ""
        except TypeError:  # pdfplumber < 0.11: no ratio / direction options
            raw = page.extract_text() or ""
        return join_japanese(raw)

    def words(self) -> List[Tuple[float, float, float, float, str]]:
        """(x0, top, x1, bottom, text) for every word."""
        try:
            found = self._page.extract_words(x_tolerance_ratio=env_float("DOCEXTRACT_X_TOLERANCE_RATIO", 0.25))
        except TypeError:
            found = self._page.extract_words()
        return [(float(w["x0"]), float(w["top"]), float(w["x1"]), float(w["bottom"]), str(w["text"])) for w in found]

    def horizontal_rules(self, min_width: float = 20.0) -> List[float]:
        """y of horizontal ruling lines and of the top/bottom edges of wide rectangles."""
        ys: List[float] = []
        for line in self._page.lines:
            if abs(float(line["top"]) - float(line["bottom"])) <= 1.0 and abs(float(line["x1"]) - float(line["x0"])) > min_width:
                ys.append(float(line["top"]))
        for rect in self._page.rects:
            if float(rect["x1"]) - float(rect["x0"]) > min_width:
                ys.extend([float(rect["top"]), float(rect["bottom"])])
        return ys

    def image_boxes(self, min_area_ratio: float = 0.0) -> List[BBox]:
        area = self.width * self.height or 1.0
        boxes: List[BBox] = []
        for image in self._page.images:
            box = (float(image["x0"]), float(image["top"]), float(image["x1"]), float(image["bottom"]))
            if max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1]) / area >= min_area_ratio:
                boxes.append(box)
        return boxes

    def render(self, dpi: int = 300, clip: Optional[BBox] = None):
        """PIL RGB image of the page (or of `clip`), rendered by pypdfium2."""
        page = self._page
        if clip is not None:
            x0, y0, x1, y1 = clip
            bounded = (max(0.0, x0), max(0.0, y0), min(self.width, x1), min(self.height, y1))
            if bounded[2] <= bounded[0] or bounded[3] <= bounded[1]:
                raise ValueError("clip is outside the page")
            page = page.crop(bounded)
        pixels = (float(page.width) * dpi / 72.0) * (float(page.height) * dpi / 72.0)
        if pixels > _MAX_RENDER_PIXELS:
            # Oversized pages (huge media boxes) would allocate gigabytes.
            dpi = max(36, int(dpi * (_MAX_RENDER_PIXELS / pixels) ** 0.5))
        return page.to_image(resolution=dpi).original.convert("RGB")

    def tables(self, borderless: Optional[bool] = None):
        from docextract.tables import pdfplumber_page_tables

        return pdfplumber_page_tables(self._page, self.number, borderless=borderless)

    def close(self) -> None:
        """Free the parsed objects of this page (long PDFs)."""
        try:
            self._page.close()
        except Exception:  # noqa: BLE001
            pass


class PdfDocument:
    def __init__(self, pdf):
        self._pdf = pdf
        self.pages = [PdfPage(page, index + 1) for index, page in enumerate(pdf.pages)]

    def __len__(self) -> int:
        return len(self.pages)


@contextmanager
def open_pdf(path) -> Iterator[PdfDocument]:
    try:
        import pdfplumber
    except ImportError as exc:
        raise PdfUnavailable("pdfplumber is required: pip install -e libs/docextract[light]") from exc
    with pdfplumber.open(str(path)) as pdf:
        limit = int(env_float("DOCEXTRACT_MAX_PAGES", 3000))
        if limit > 0 and len(pdf.pages) > limit:
            raise PdfTooLarge(f"{len(pdf.pages)} pages (DOCEXTRACT_MAX_PAGES={limit})")
        yield PdfDocument(pdf)
