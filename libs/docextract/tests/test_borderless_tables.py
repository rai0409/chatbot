"""Borderless (text-alignment) table detection must not cut running text into cells."""
from __future__ import annotations

import pytest

pytest.importorskip("pdfplumber")

from docextract.pdf import open_pdf  # noqa: E402
from docextract.testing import simple_pdf  # noqa: E402


def _tables(tmp_path, texts):
    path = tmp_path / "t.pdf"
    path.write_bytes(simple_pdf([{"texts": texts}]))
    with open_pdf(path) as doc:
        return doc.pages[0].tables()


def test_a_question_answer_list_is_running_text_not_a_table(tmp_path):
    lines = ["Q1. How do I apply?", "A1. Use the system.", "Q2. When is the deadline?", "A2. The end of the month",
             "Q3. Who approves it?", "A3. Your manager."]
    texts = [(60, 80 + 16 * i, 10, line) for i, line in enumerate(lines)]
    assert _tables(tmp_path, texts) == []


def test_wrapped_paragraph_lines_are_not_a_table(tmp_path):
    lines = ["Expense claims must be filed within ten", "days of the purchase date by the employee", "using the form that is provided by",
             "the accounting department of the company"]
    texts = [(60, 80 + 16 * i, 10, line) for i, line in enumerate(lines)]
    assert _tables(tmp_path, texts) == []


def test_columns_separated_by_wide_gaps_are_still_a_table(tmp_path):
    rows = [("Item", "Limit", "Note"), ("Lodging", "12000", "per night"), ("Daily", "3000", "per day"), ("Meeting", "5000", "per person")]
    texts = []
    for i, row in enumerate(rows):
        for x, cell in zip((60, 220, 340), row):
            texts.append((x, 80 + 18 * i, 10, cell))
    found = _tables(tmp_path, texts)
    assert len(found) == 1
    assert found[0].rows[1][0] == "Lodging" and "12000" in found[0].rows[1]
