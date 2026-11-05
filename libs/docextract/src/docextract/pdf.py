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
  column by column, right to left, each column top to bottom (_vertical_text)
"""

from __future__ import annotations

import os
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
    """More pages than DOCEXTRACT_MAX_PAGES, or a file over DOCEXTRACT_MAX_PDF_BYTES."""


class PdfEncrypted(ValueError):
    """The PDF needs a password (or uses an encryption this reader cannot open)."""


# Rendering (OCR) is scaled down above this many pixels, like images.py.
_MAX_RENDER_PIXELS = 60_000_000


def _inside(obj, boxes: Sequence[BBox]) -> bool:
    cx = (float(obj["x0"]) + float(obj["x1"])) / 2
    cy = (float(obj["top"]) + float(obj["bottom"])) / 2
    return any(x0 <= cx <= x1 and y0 <= cy <= y1 for x0, y0, x1, y1 in boxes)


def join_japanese(text: str) -> str:
    return _JA_GAP_RE.sub("", text or "")


# A figure / table caption, a note or a source line is set larger than the body now and then, but it is not a
# heading. The label must be followed by a number or a colon (or, for 出典 / 備考, by the text itself), so a bare
# "備考" or "注意事項" section title is still a heading.
_CAPTION_RE = re.compile(
    r"^[※＊*]?\s*(?:(?:図|表|写真|注|Fig\.?|Figure|Table|Note|Photo)\s*[0-9０-９一二三四五六七八九十]+|"
    r"(?:出典|備考|注記|Source|Notes?)\s*[:：]|(?:出典|備考|注記)\s+\S|[※＊*]\s*注)",
    re.IGNORECASE,
)


def is_caption_line(text: str) -> bool:
    return bool(_CAPTION_RE.match(text.strip()))


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

    @staticmethod
    def _vertical_text(chars) -> str:
        """縦書き text from characters: columns right to left, each column top to bottom, one column per line.
        (pdfplumber's own char_dir/line_dir options read such pages row by row.)"""
        glyphs = [c for c in chars if str(c.get("text") or "").strip()]
        if not glyphs:
            return ""
        sizes = sorted(float(c.get("size") or 0) or (float(c["bottom"]) - float(c["top"])) for c in glyphs)
        size = max(sizes[len(sizes) // 2], 1.0)
        glyphs.sort(key=lambda c: -(float(c["x0"]) + float(c["x1"])) / 2)  # right to left
        columns: List[List] = []
        last_x = None
        for glyph in glyphs:
            x = (float(glyph["x0"]) + float(glyph["x1"])) / 2
            if last_x is None or last_x - x > 0.6 * size:  # a new column starts
                columns.append([])
                last_x = x
            columns[-1].append(glyph)
        return "\n".join("".join(str(g["text"]) for g in sorted(col, key=lambda g: float(g["top"]))) for col in columns)

    def text(self, exclude: Sequence[BBox] = ()) -> str:
        """Page text in reading order, without characters inside `exclude`."""
        page = self._page
        boxes = [tuple(map(float, b)) for b in exclude if b]
        if boxes:
            page = page.filter(lambda obj: not (obj.get("object_type") == "char" and _inside(obj, boxes)))
        if self.is_vertical():
            return self._vertical_text(page.chars)
        kwargs = {"x_tolerance_ratio": env_float("DOCEXTRACT_X_TOLERANCE_RATIO", 0.25), "y_tolerance": 3}
        try:
            raw = page.extract_text(**kwargs) or ""
        except TypeError:  # pdfplumber < 0.11: no ratio / direction options
            raw = page.extract_text() or ""
        return join_japanese(raw)

    def heading_lines(self, ratio: Optional[float] = None, max_chars: int = 40) -> List[str]:
        """Short lines that look like headings: set in a larger font than the body (ratio x the median size by
        characters), or in a bold face. Lines that end a sentence, numeric lines, figure / table captions and
        notes (is_caption_line) and vertical pages are never headings, and a page where more than half of the
        lines qualify (all bold, a poster) has none. Returns the lines' text, spaces between Japanese characters
        removed as in text().

        The default ratio comes from tools/eval_heading_detection.py on generated pages: 1.08 finds headings that
        are 10% larger than the body, and body text whose size varies by +-6% is still never taken for one (1.05
        produced false headings there; the former 1.12 missed every 10%-larger heading)."""
        if ratio is None:
            ratio = env_float("DOCEXTRACT_HEADING_RATIO", 1.08)
        if self.is_vertical():
            return []
        try:
            lines = self._page.extract_text_lines(return_chars=True, strip=True)
        except (AttributeError, TypeError):  # pdfplumber too old for extract_text_lines
            return []
        sized = []
        for line in lines:
            chars = [c for c in line.get("chars", []) if str(c.get("text") or "").strip()]
            if not chars:
                continue
            sizes = sorted(float(c.get("size") or 0) for c in chars)
            bold = sum(1 for c in chars if re.search(r"bold|black|heavy|-w[6-9]\b", str(c.get("fontname") or ""), re.I)) > len(chars) / 2
            raw = str(line.get("text") or "").strip()
            # the caption check looks at the raw line: join_japanese removes the space of "備考 本表は…"
            sized.append((join_japanese(raw).strip(), sizes[len(sizes) // 2], bold, len(chars), raw))
        if len(sized) < 3:
            return []
        weighted = sorted(size for _text, size, _bold, count, _raw in sized for _ in range(count))
        body = weighted[len(weighted) // 2]
        found = []
        for text, size, bold, _count, raw in sized:
            if not text or len(text) > max_chars or text[-1] in "。.!?！？" or re.fullmatch(r"[\d\s\-–—/.,:()（）]+", text):
                continue
            if is_caption_line(raw) or is_caption_line(text):
                continue
            if size >= body * ratio or (bold and size >= body * 0.98 and body > 0):
                found.append(text)
        return [] if len(found) > max(4, int(len(sized) * 0.5)) else found

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
            # Round down so the cap holds; below 1 dpi nothing readable is left.
            dpi = int(dpi * (_MAX_RENDER_PIXELS / pixels) ** 0.5)
            if dpi < 1:
                raise ValueError("page is too large to render")
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


_ENCRYPTION_ERRORS = ("PDFPasswordIncorrect", "PDFEncryptionError")


@contextmanager
def open_pdf(path) -> Iterator[PdfDocument]:
    max_bytes = int(env_float("DOCEXTRACT_MAX_PDF_BYTES", 200 * 1024 * 1024))
    if max_bytes > 0 and os.path.getsize(str(path)) > max_bytes:
        raise PdfTooLarge(f"file is larger than DOCEXTRACT_MAX_PDF_BYTES={max_bytes}")
    try:
        import pdfplumber
    except ImportError as exc:
        raise PdfUnavailable("pdfplumber is required: pip install -e libs/docextract[light]") from exc
    try:
        handle = pdfplumber.open(str(path))
    except Exception as exc:  # noqa: BLE001 - pdfminer raises its own types
        names = {cls.__name__ for cls in type(exc).__mro__}
        if names & set(_ENCRYPTION_ERRORS) or "password" in str(exc).lower():
            raise PdfEncrypted("the PDF is encrypted or needs a password") from exc
        raise
    with handle as pdf:
        limit = int(env_float("DOCEXTRACT_MAX_PAGES", 3000))
        if limit > 0 and len(pdf.pages) > limit:
            raise PdfTooLarge(f"{len(pdf.pages)} pages (DOCEXTRACT_MAX_PAGES={limit})")
        yield PdfDocument(pdf)
