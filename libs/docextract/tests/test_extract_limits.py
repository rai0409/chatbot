"""PR D: PDF / OCR / table-HTML limits. Synthetic inputs only."""
from __future__ import annotations

import sys
import types

import pytest

from docextract import _util, ocr, pdf


# --- PDF -------------------------------------------------------------------------------------------


def test_pdf_file_size_cap(tmp_path, monkeypatch):
    path = tmp_path / "big.pdf"
    path.write_bytes(b"%PDF-1.7\n" + b"0" * 5000)
    monkeypatch.setenv("DOCEXTRACT_MAX_PDF_BYTES", "1000")
    with pytest.raises(pdf.PdfTooLarge):
        with pdf.open_pdf(path):
            pass


def test_encrypted_pdf_has_its_own_error(tmp_path, monkeypatch):
    class PDFPasswordIncorrect(Exception):
        pass

    fake = types.ModuleType("pdfplumber")

    def _open(_path):
        raise PDFPasswordIncorrect("needs a password")

    fake.open = _open
    monkeypatch.setitem(sys.modules, "pdfplumber", fake)
    path = tmp_path / "locked.pdf"
    path.write_bytes(b"%PDF-1.7\n")
    with pytest.raises(pdf.PdfEncrypted):
        with pdf.open_pdf(path):
            pass


class _FakePage:
    width = 100000.0
    height = 100000.0

    def __init__(self):
        self.dpi = None

    def to_image(self, resolution):
        self.dpi = resolution
        raise RuntimeError("stop before allocating")


def test_render_never_exceeds_the_pixel_cap():
    fake = _FakePage()
    page = pdf.PdfPage(fake, 1)
    with pytest.raises(RuntimeError):
        page.render(dpi=300)
    pixels = (fake.width * fake.dpi / 72.0) * (fake.height * fake.dpi / 72.0)
    assert pixels <= pdf._MAX_RENDER_PIXELS


def test_render_refuses_a_page_that_cannot_fit():
    fake = _FakePage()
    fake.width = fake.height = 10_000_000.0
    with pytest.raises(ValueError):
        pdf.PdfPage(fake, 1).render(dpi=300)


# --- OCR -------------------------------------------------------------------------------------------


def test_tesseract_upscale_is_capped(monkeypatch):
    pytest.importorskip("PIL")
    from PIL import Image

    monkeypatch.setenv("DOCEXTRACT_OCR_MAX_PIXELS", "1000000")
    prepared = ocr._prepare_for_tesseract(Image.new("RGB", (3000, 300), (255, 255, 255)))
    assert prepared.width * prepared.height <= 1_000_000


def test_tesseract_gets_a_timeout(monkeypatch):
    calls = {}
    fake = types.ModuleType("pytesseract")
    fake.Output = types.SimpleNamespace(DICT="dict")

    def image_to_data(image, **kwargs):
        calls.update(kwargs)
        return {"text": [], "conf": []}

    fake.image_to_data = image_to_data
    monkeypatch.setitem(sys.modules, "pytesseract", fake)
    monkeypatch.setenv("DOCEXTRACT_OCR_TIMEOUT_SECONDS", "7")
    ocr._tesseract_pass(object(), "jpn", "3")
    assert calls.get("timeout") == 7


# --- table HTML ------------------------------------------------------------------------------------


def test_html_tables_normal_input_unchanged():
    html = "<table><tr><th>名前</th><th>値</th></tr><tr><td>山田太郎</td><td>1</td></tr></table>"
    assert _util.html_tables(html) == [[["名前", "値"], ["山田太郎", "1"]]]


def test_html_tables_deep_nesting_is_refused():
    html = "<table><tr><td>" * 200 + "x" + "</td></tr></table>" * 200
    assert _util.html_tables(html) == []


def test_html_tables_span_expansion_is_bounded():
    cell = '<td rowspan="50" colspan="50">a</td>'
    html = "<table><tr>" + cell * 2000 + "</tr></table>"
    assert _util.html_tables(html) == []


def test_html_tables_input_size_cap(monkeypatch):
    monkeypatch.setenv("DOCEXTRACT_MAX_TABLE_HTML_CHARS", "100")
    html = "<table><tr><td>" + "a" * 500 + "</td></tr></table>"
    assert _util.html_tables(html) == []
