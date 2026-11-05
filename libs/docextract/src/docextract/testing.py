"""Fixtures for tests and examples (no third-party dependency).

simple_pdf() writes a small valid PDF with Helvetica text, lines and
rectangles (coordinates in points, origin top-left like pdfplumber), so
tests do not need a PDF library to create their input.
japanese_text_image() renders text with a local Japanese font (for OCR tests).
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Sequence

_FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "/mnt/c/Windows/Fonts/meiryo.ttc",
    "C:/Windows/Fonts/meiryo.ttc",
)


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def simple_pdf(pages: Sequence[Dict]) -> bytes:
    """pages: [{"size": (w, h), "texts": [(x, top, size, text)],
    "lines": [(x0, top0, x1, top1)], "rects": [(x0, top, x1, bottom)]}]."""
    objects: List[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")  # filled below
    pages_obj = add(b"")
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    kids: List[int] = []
    for page in pages:
        width, height = page.get("size", (595, 842))
        ops: List[str] = []
        for x0, t0, x1, t1 in page.get("lines", ()):
            ops.append(f"{x0} {height - t0} m {x1} {height - t1} l S")
        for x0, top, x1, bottom in page.get("rects", ()):
            ops.append(f"{x0} {height - bottom} {x1 - x0} {bottom - top} re S")
        for x, top, size, text in page.get("texts", ()):
            ops.append(f"BT /F1 {size} Tf {x} {height - top - size} Td ({_escape(text)}) Tj ET")
        stream = "\n".join(ops).encode("latin-1")
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        kids.append(add(
            f"<< /Type /Page /Parent {pages_obj} 0 R /MediaBox [0 0 {width} {height}] "
            f"/Resources << /Font << /F1 {font} 0 R >> >> /Contents {content} 0 R >>".encode("ascii")
        ))
    objects[catalog - 1] = f"<< /Type /Catalog /Pages {pages_obj} 0 R >>".encode("ascii")
    objects[pages_obj - 1] = (
        f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode("ascii")
    )
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii")
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
    return bytes(out)


def japanese_font(path: Optional[str] = None) -> Optional[str]:
    for candidate in ([path] if path else []) + [os.getenv("DOCEXTRACT_TEST_FONT", "")] + list(_FONT_CANDIDATES):
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def japanese_text_image(lines: Sequence[str], font_path: Optional[str] = None, size: int = 40):
    """PIL image with the lines in black on white, or None without a font."""
    font_file = japanese_font(font_path)
    if font_file is None:
        return None
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(font_file, size)
    width = max(int(font.getlength(line)) for line in lines) + 2 * size
    height = (len(lines) + 1) * int(size * 1.6)
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    for i, line in enumerate(lines):
        draw.text((size, size // 2 + i * int(size * 1.6)), line, font=font, fill="black")
    return image
