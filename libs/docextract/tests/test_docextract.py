"""docextract without heavy backends: helpers, fake pdfplumber pages, fallbacks."""
from __future__ import annotations

from pathlib import Path

import pytest

from docextract import ocr, tables
from docextract._util import clean_grid, detect_header_rows, fill_merged_header, html_tables, plausible_borderless
from docextract.images import load_image, vector_format
from docextract.models import Table, TableRegion


def test_html_table_spans_and_nested_tables():
    grids = html_tables(
        "<table><tr><th colspan=2>区分</th><th rowspan=2>備考</th></tr>"
        "<tr><td>A</td><td>B<table><tr><td>内</td></tr></table></td></tr></table>"
    )
    assert [["区分", "区分", "備考"], ["A", "B内", "備考"]] in grids
    assert [["内"]] in grids


def test_grid_cleanup_and_two_row_header():
    grid = clean_grid([["費用", None, "備考", ""], ["交通", "宿泊", "メモ", ""], [None, None, None, None], ["100", "200", "なし", ""]])
    assert grid == [["費用", "", "備考"], ["交通", "宿泊", "メモ"], ["100", "200", "なし"]]
    grid = fill_merged_header(grid)
    assert grid[0] == ["費用", "費用", "備考"]
    assert detect_header_rows(grid) == 2
    table = Table(rows=grid, regions=[TableRegion(1)], backend="x", header_rows=2)
    assert table.header() == ["費用/交通", "費用/宿泊", "備考/メモ"]
    assert table.body() == [["100", "200", "なし"]]


def test_borderless_plausibility_rejects_prose():
    assert plausible_borderless([["項目", "金額"], ["交通費", "1,200"], ["宿泊費", "8,000"]]) >= 0.65
    prose = [["この規程は社員の出張に関する手続きを定めるものであり、適用範囲は全社員とする。", "x"], ["a", "b"], ["c", ""]]
    assert plausible_borderless(prose) == 0.0


def test_continued_table_is_merged_and_repeated_header_dropped():
    first = Table(rows=[["品目", "数量"], ["机", "1"]], regions=[TableRegion(1, (0, 600, 500, 780))], backend="x")
    second = Table(rows=[["品目", "数量"], ["椅子", "2"]], regions=[TableRegion(2, (0, 40, 500, 200))], backend="x")
    other = Table(rows=[["a", "b", "c"], ["1", "2", "3"]], regions=[TableRegion(3, (0, 40, 500, 200))], backend="x")
    merged = tables.merge_continued_tables([first, second, other], {1: 800.0, 2: 800.0, 3: 800.0})
    assert len(merged) == 2
    assert merged[0].rows == [["品目", "数量"], ["机", "1"], ["椅子", "2"]]
    assert merged[0].pages == [1, 2]


class _FakeTable:
    def __init__(self, bbox, rows):
        self.bbox = bbox
        self._rows = rows

    def extract(self):
        return self._rows


class _FakePage:
    """pdfplumber-like page: find_tables() = ruled, table_settings = text strategy."""

    def __init__(self, ruled, text):
        self._ruled, self._text = ruled, text

    def find_tables(self, table_settings=None):
        return self._text if table_settings else self._ruled


def test_pdfplumber_page_ruled_then_borderless():
    ruled = _FakePage([_FakeTable((0, 0, 10, 10), [["品目", "数量"], ["机", "3"]])], [])
    found = tables.pdfplumber_page_tables(ruled, 1)
    assert found[0].backend == "pdfplumber:lines" and found[0].rows == [["品目", "数量"], ["机", "3"]]
    borderless = _FakePage([], [_FakeTable((0, 0, 10, 10), [["項目", "金額"], ["交通費", "1,200"], ["宿泊費", "8,000"]])])
    found = tables.pdfplumber_page_tables(borderless, 2)
    assert found and found[0].backend == "pdfplumber:text" and found[0].confidence < 0.9
    assert tables.pdfplumber_page_tables(borderless, 2, borderless=False) == []
    assert tables.pdfplumber_page_tables(object(), 3) == []


def test_no_pymupdf_anywhere():
    import docextract

    src = Path(docextract.__file__).parent
    for path in src.glob("*.py"):
        code = path.read_text(encoding="utf-8")
        assert "import fitz" not in code and "pymupdf_page_tables" not in code, path.name
    assert "pymupdf" not in tables._BACKENDS


def test_backend_selection(monkeypatch):
    monkeypatch.setenv("DOCEXTRACT_TABLE_BACKEND", "none")
    assert tables.resolve_backend() is None
    monkeypatch.setenv("DOCEXTRACT_TABLE_BACKEND", "auto")
    monkeypatch.setenv("DOCEXTRACT_TABLE_PREFERENCE", "nonexistent")
    assert tables.resolve_backend() is None
    monkeypatch.setattr(tables, "backend_available", lambda name: name == "pdfplumber")
    monkeypatch.setenv("DOCEXTRACT_TABLE_PREFERENCE", "docling,pdfplumber")
    assert tables.resolve_backend() == "pdfplumber"


def test_runtime_failure_falls_back_to_next_backend(monkeypatch):
    monkeypatch.setenv("DOCEXTRACT_TABLE_BACKEND", "auto")
    monkeypatch.setenv("DOCEXTRACT_TABLE_PREFERENCE", "docling,pdfplumber")
    monkeypatch.setattr(tables, "backend_available", lambda name: name in ("docling", "pdfplumber"))

    def broken(path, pages):
        raise RuntimeError("model download failed")

    good = Table(rows=[["a", "b"], ["1", "2"]], regions=[TableRegion(1, None)], backend="pdfplumber:lines")
    monkeypatch.setitem(tables._BACKENDS, "docling", broken)
    monkeypatch.setitem(tables._BACKENDS, "pdfplumber", lambda path, pages: [good])
    monkeypatch.setattr(tables, "_page_heights", lambda path: {})
    found = tables.extract_pdf_tables("x.pdf")
    assert found["backend"] == "pdfplumber" and found["tables"] == [good]
    assert found["errors"] and found["errors"][0].startswith("docling")


def test_ocr_without_engines_returns_empty_with_reasons(monkeypatch):
    monkeypatch.setenv("DOCEXTRACT_OCR_ENGINES", "tesseract")
    monkeypatch.setattr(ocr, "tesseract_languages", lambda: frozenset())
    result = ocr.ocr_image(object())
    assert not result.ok and result.errors
    assert ocr.ocr_image(b"not an image").errors  # undecodable or pillow_missing


def test_ocr_falls_back_to_the_next_engine(monkeypatch):
    from docextract.models import OcrLine, OcrResult

    monkeypatch.setitem(ocr._RUNNERS, "paddle", lambda image, **_: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setitem(ocr._RUNNERS, "tesseract", lambda image, **_: OcrResult("読めた", "tesseract", 80.0, [OcrLine("読めた", 80.0)]))
    result = ocr.ocr_image(object(), engines=["paddle", "tesseract"])
    assert result.text == "読めた" and result.engine == "tesseract"
    assert len(result.errors) == 1 and result.errors[0].startswith("paddle: RuntimeError")


def test_yomitoku_needs_the_license_flag(monkeypatch):
    monkeypatch.delenv("DOCEXTRACT_YOMITOKU_LICENSED", raising=False)
    assert not ocr.engine_available("yomitoku")
    assert not tables.backend_available("yomitoku")


def test_metafile_detection():
    emf = b"\x01\x00\x00\x00" + b"\x00" * 36 + b" EMF" + b"\x00" * 8
    assert vector_format(emf) == "emf"
    assert vector_format(b"\xd7\xcd\xc6\x9a" + b"\x00" * 20) == "wmf"
    assert vector_format(b"\x89PNG\r\n\x1a\n") is None
    assert vector_format(b"", "figure.EMF") == "emf"
    image, reason = load_image(b"garbage")
    assert image is None and reason in ("undecodable", "pillow_missing")


def test_health_report_shape():
    from docextract.health import report

    result = report()
    assert {"table_backend", "ocr_engines_installed", "packages", "warnings"} <= set(result)
    assert "PyMuPDF" in result["packages"]


# --- real PDFs (pdfplumber + pypdfium2) -------------------------------------------------------------


def _grid_page():
    rows = [("Item", "Qty"), ("Desk", "3"), ("Chair", "12")]
    texts = [(72, 60, 11, "CONFIDENTIAL"), (72, 90, 11, "Backups are verified every morning.")]
    lines, top = [], 140
    for i, (a, b) in enumerate(rows):
        y = top + i * 24
        texts += [(80, y + 6, 10, a), (230, y + 6, 10, b)]
    for i in range(len(rows) + 1):
        lines.append((72, top + i * 24, 372, top + i * 24))
    for x in (72, 222, 372):
        lines.append((x, top, x, top + 24 * len(rows)))
    return {"texts": texts, "lines": lines}


def test_pdf_text_tables_rules_and_render(tmp_path):
    pytest.importorskip("pdfplumber")
    pytest.importorskip("pypdfium2")
    from docextract.pdf import open_pdf
    from docextract.testing import simple_pdf

    path = tmp_path / "doc.pdf"
    path.write_bytes(simple_pdf([_grid_page(), {"texts": [(72, 72, 12, "Second page")]}]))
    with open_pdf(path) as doc:
        assert len(doc) == 2
        page = doc.pages[0]
        found = page.tables()
        assert len(found) == 1 and found[0].rows == [["Item", "Qty"], ["Desk", "3"], ["Chair", "12"]]
        outside = page.text(exclude=[found[0].regions[0].bbox])
        assert "Backups are verified" in outside and "Desk" not in outside
        assert any(abs(y - 140) < 1 for y in page.horizontal_rules())
        words = [w[4] for w in page.words()]
        assert "CONFIDENTIAL" in words
        image = page.render(dpi=72)
        assert image.size == (595, 842)
        assert page.render(dpi=144, clip=(72, 140, 372, 212)).size == (600, 144)
        assert doc.pages[1].text().strip() == "Second page"
        assert page.vertical_ratio() < 0.5


def test_extract_pdf_tables_with_pdfplumber_backend(tmp_path, monkeypatch):
    pytest.importorskip("pdfplumber")
    from docextract.testing import simple_pdf

    monkeypatch.setenv("DOCEXTRACT_TABLE_BACKEND", "pdfplumber")
    path = tmp_path / "doc.pdf"
    path.write_bytes(simple_pdf([_grid_page()]))
    found = tables.extract_pdf_tables(str(path))
    assert found["backend"] == "pdfplumber" and found["tables"][0].header() == ["Item", "Qty"]


def test_japanese_spacing_is_joined():
    from docextract.pdf import join_japanese

    assert join_japanese("経費 精算 の 締め日") == "経費精算の締め日"
    assert join_japanese("PDF ファイル と Excel") == "PDF ファイルと Excel"


# --- PaddleOCR PP-OCRv6 vs PP-OCRv5 (heavy: DOCEXTRACT_RUN_MODEL_TESTS=1) ---------------------------


def test_pp_ocrv6_reads_japanese(monkeypatch):
    import os

    if os.getenv("DOCEXTRACT_RUN_MODEL_TESTS") != "1":
        pytest.skip("model test (downloads PaddleOCR models): set DOCEXTRACT_RUN_MODEL_TESTS=1")
    pytest.importorskip("paddleocr")
    from docextract.benchmark import compare
    from docextract.testing import japanese_text_image

    lines = ["経費精算の締め日は毎月25日です。", "申請は総務部の承認後に支払われます。"]
    image = japanese_text_image(lines)
    if image is None:
        pytest.skip("no Japanese font (set DOCEXTRACT_TEST_FONT)")
    # A degraded copy (small text, blur, noise) like a poor scan.
    import random

    from PIL import ImageFilter

    small = japanese_text_image(lines, size=16)
    noisy = small.resize((small.width * 2, small.height * 2)).filter(ImageFilter.GaussianBlur(1.6))
    rng = random.Random(7)
    pixels = noisy.load()
    for _ in range(noisy.width * noisy.height // 25):
        x, y = rng.randrange(noisy.width), rng.randrange(noisy.height)
        pixels[x, y] = (rng.randrange(256),) * 3
    expected = ["".join(lines)] * 2
    rows = compare([image, noisy], engines=["paddle"], paddle_versions=["PP-OCRv6", "PP-OCRv5"], expected=expected)
    for r in rows:
        print(r["config"]["DOCEXTRACT_PADDLE_OCR_VERSION"], "clean" if r["image"] == 0 else "degraded",
              r["char_accuracy"], r["confidence"], r["seconds"])
    v6 = [r for r in rows if r["config"]["DOCEXTRACT_PADDLE_OCR_VERSION"] == "PP-OCRv6"]
    assert all(r["engine"] == "paddle:PP-OCRv6" for r in v6)
    assert v6[0]["char_accuracy"] >= 0.9
