"""Extraction / preprocessing on generated PDFs and strings: header order, headings by font, display characters,
fiscal years and times. Synthetic data only (ASCII PDFs from docextract.testing; no real documents)."""
from __future__ import annotations

import pytest

from rag_core import extraction, ja_text
from rag_core.quantities import extract_quantities
from scripts import pdf_to_canonical_jsonl as conv

pytest.importorskip("pdfplumber")
extraction.load()  # puts libs/docextract on the path
from docextract.testing import simple_pdf  # noqa: E402


def _page(n, body_lines, extra=()):
    texts = [(60, 30, 9, "ACME Corp Internal Rules"), (270, 800, 9, f"- {n} -")]
    top = 80
    for size, line in body_lines:
        texts.append((60, top, size, line))
        top += int(size * 1.6)
    return {"texts": texts + list(extra)}


def _convert(tmp_path, pages, doc_type="policy"):
    path = tmp_path / "doc.pdf"
    path.write_bytes(simple_pdf(pages))
    rows, report = conv.convert_pdf_to_rows(path, doc_type=doc_type, ocr=False, source_doc="doc.pdf")
    return rows, report


# --- header / footer removal happens before wrapped lines are joined --------------------------------------------------------------


def test_a_repeated_header_is_removed_and_never_glued_to_the_next_line(tmp_path):
    # different words on every page: digits are masked when repeated lines are detected, so "month 1" == "month 2"
    topics = {1: "travel", 2: "lodging", 3: "meetings"}
    pages = [_page(n, [(10, f"Expense claims for {topics[n]} must be filed within"), (10, f"ten days of the purchase date for {topics[n]}.")])
             for n in (1, 2, 3)]
    rows, _ = _convert(tmp_path, pages)
    text = "\n".join(r["text"] for r in rows)
    assert "ACME" not in text and "- 1 -" not in text
    assert "for lodging must be filed within ten days of the purchase date for lodging." in text


def test_a_header_shorter_than_the_threshold_is_removed_too(tmp_path):
    pages = [{"texts": [(60, 30, 9, "Internal"), (60, 80, 10, "This is the body of the page and it is long enough."),
                        (270, 800, 9, f"- {n} -")]} for n in (1, 2, 3)]
    rows, _ = _convert(tmp_path, pages)
    assert "Internal" not in "\n".join(r["text"] for r in rows)


# --- headings recognised by their font size -----------------------------------------------------------------------------------------


def _manual_pages():
    return [_page(n, [
        (18, "Expense Policy"),
        (10, "General rules apply to every employee of the company."),
        (13, "Procedure"),
        (10, "Create the request in the system and attach receipts."),
        (10, "Ask your manager for approval before the deadline."),
        (13, "Notes"),
        (10, "Private expenses must never be included in a claim."),
    ]) for n in (1, 2, 3)]


def test_larger_lines_become_section_headings(tmp_path):
    rows, _ = _convert(tmp_path, _manual_pages())
    paths = {tuple(r.get("section_path") or ()) for r in rows if r.get("chunk_role") == "parent"}
    flat = " | ".join(" > ".join(p) for p in paths)
    assert "Procedure" in flat and "Notes" in flat


def test_a_section_body_does_not_contain_the_next_heading_or_its_text(tmp_path):
    rows, _ = _convert(tmp_path, _manual_pages())
    procedure = [r for r in rows if r.get("chunk_role") == "parent" and "Procedure" in " ".join(r.get("section_path") or [])]
    assert procedure
    for row in procedure:
        assert "Private expenses" not in row["text"] and "Notes" not in row["text"]
        assert "attach receipts" in row["text"] or "approval" in row["text"]


def test_a_page_of_uniform_text_gets_no_headings(tmp_path):
    pages = [_page(n, [(10, "Line one of a plain paragraph that wraps over"), (10, "two lines of the same size.")]) for n in (1, 2, 3)]
    rows, _ = _convert(tmp_path, pages)
    assert not any("■" in " ".join(r.get("section_path") or []) for r in rows)


def test_headings_are_not_added_to_a_qa_document(tmp_path):
    pages = [_page(1, [(14, "Frequently asked"), (10, "Q: How do I apply?"), (10, "A: Use the system.")])]
    rows, _ = _convert(tmp_path, pages, doc_type="faq")
    assert any(r.get("faq_question") == "How do I apply?" for r in rows)


# --- display characters ----------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("char", ["㈱", "㈲", "㎏", "㎡", "℃", "Ⅲ", "ⅲ", "㍻", "№"])
def test_display_keeps_symbols_that_nfkc_would_rewrite(char):
    assert ja_text.nfkc_keep_circled(f"A{char}B") == f"A{char}B"
    assert char not in ja_text.fold_circled(f"A{char}B")  # the search form is folded


def test_the_rest_is_still_folded_for_display():
    assert ja_text.nfkc_keep_circled("ＡＢＣ１２３　ｶﾞｲﾄﾞ") == "ABC123 ガイド"


def test_stored_text_keeps_the_symbols_and_search_text_folds_them():
    from rag_core.chunking_ja import build_ja_chunk_records

    rows = build_ja_chunk_records(doc_id="d", source_doc="s.pdf", text="㈱サンプルの荷物は10㎏、温度5℃、第Ⅲ章を参照。",
                                  doc_type="policy", title="t", source_pages=[1])
    assert all("㈱" in r["text"] and "10㎏" in r["text"] and "5℃" in r["text"] and "Ⅲ" in r["text"] for r in rows)
    assert all("㈱" not in r["searchable_text"] and "(株)" in r["searchable_text"] and "10kg" in r["searchable_text"]
               for r in rows)


# --- fiscal years and times -------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("text, canonical", [
    ("2024年度末まで適用する", "FY2024"),
    ("令和6年度の予算", "FY2024"),
    ("平成30年度に開始", "FY2018"),
])
def test_fiscal_years(text, canonical):
    found = [(q.kind, q.canonical) for q in extract_quantities(text)]
    assert ("fiscal_year", canonical) in found
    assert not any(kind == "date" for kind, _ in found)  # not also read as the calendar year


@pytest.mark.parametrize("text, canonical", [
    ("午後3時30分から開始", "15:30"),
    ("午前9時に集合", "09:00"),
    ("午後10時以降は禁止", "22:00"),
    ("15時30分まで", "15:30"),
    ("午前0時に更新", "00:00"),
    ("午後12時30分", "12:30"),
])
def test_times_of_day(text, canonical):
    assert ("time", canonical) in [(q.kind, q.canonical) for q in extract_quantities(text)]


@pytest.mark.parametrize("text", ["3時間かかる", "2時間30分以内", "作業時間は30分", "25時30分"])
def test_durations_and_impossible_times_are_not_times(text):
    assert not any(q.kind == "time" for q in extract_quantities(text))


def test_a_time_does_not_leave_its_minutes_as_a_second_quantity():
    kinds = [q.kind for q in extract_quantities("午後3時30分から")]
    assert kinds.count("time") == 1 and "duration" not in kinds and "number" not in kinds


# --- an answer without a final 。 does not swallow the next question -----------------------------------------------------------------


@pytest.mark.parametrize("prev, nxt", [
    ("申請はシステムの経費メニューから行います", "Q2. 領収書をなくした場合は"),
    ("申請はシステムの経費メニューから行います", "質問2：領収書をなくした場合は"),
    ("経費の精算は所定の様式により申請しなければならない", "第2条（申請期限）"),
    ("再発行を依頼するか支払証明書を作成して上長の承認を受ける", "回答：再発行を依頼する"),
])
def test_a_new_question_answer_or_article_starts_a_new_line(prev, nxt):
    from rag_core.line_joining import join_separator

    assert join_separator(prev, nxt) is None
