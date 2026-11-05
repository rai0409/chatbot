"""縦書き: columns are read right to left, each column top to bottom."""
from __future__ import annotations

import pytest

pytest.importorskip("pdfplumber")

from docextract.pdf import open_pdf  # noqa: E402
from docextract.testing import simple_pdf  # noqa: E402


def _vertical_pdf(tmp_path, columns, x0=420, gap=40, size=12, step=14):
    texts = []
    for c, column in enumerate(columns):
        for r, char in enumerate(column):
            texts.append((x0 - gap * c, 100 + step * r, size, char))
    path = tmp_path / "v.pdf"
    path.write_bytes(simple_pdf([{"texts": texts}]))
    return path


def test_columns_are_read_right_to_left_and_top_to_bottom(tmp_path):
    path = _vertical_pdf(tmp_path, ["ABCDEFGH", "IJKLMNOP", "QRSTU"])
    with open_pdf(path) as doc:
        page = doc.pages[0]
        assert page.is_vertical()
        assert page.text().splitlines() == ["ABCDEFGH", "IJKLMNOP", "QRSTU"]


def test_horizontal_text_is_untouched(tmp_path):
    path = tmp_path / "h.pdf"
    path.write_bytes(simple_pdf([{"texts": [(60, 80, 10, "first line of text"), (60, 96, 10, "second line of text")]}]))
    with open_pdf(path) as doc:
        page = doc.pages[0]
        assert not page.is_vertical()
        assert page.text().splitlines() == ["first line of text", "second line of text"]


def test_characters_in_a_column_with_slightly_different_x_stay_together(tmp_path):
    texts = []
    for r, char in enumerate("ABCDEFGHIJ"):
        texts.append((300 + (r % 3) * 0.8, 100 + 14 * r, 12, char))  # jitter inside a column
    for r, char in enumerate("KLMNOPQRST"):
        texts.append((250 - (r % 3) * 0.8, 100 + 14 * r, 12, char))
    path = tmp_path / "j.pdf"
    path.write_bytes(simple_pdf([{"texts": texts}]))
    with open_pdf(path) as doc:
        assert doc.pages[0].text().splitlines() == ["ABCDEFGHIJ", "KLMNOPQRST"]
