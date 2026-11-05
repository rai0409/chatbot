"""Font-based heading detection: threshold, captions, and the all-or-nothing guard. ASCII PDFs from simple_pdf."""
from __future__ import annotations

import pytest

pytest.importorskip("pdfplumber")

from docextract.pdf import open_pdf  # noqa: E402
from docextract.testing import simple_pdf  # noqa: E402


def _headings(tmp_path, lines, **kw):
    """lines: (size, text). Returns heading_lines() of the page."""
    texts, top = [], 80
    for size, text in lines:
        texts.append((60, top, size, text))
        top += int(size * 1.7)
    path = tmp_path / "h.pdf"
    path.write_bytes(simple_pdf([{"texts": texts}]))
    with open_pdf(path) as doc:
        return doc.pages[0].heading_lines(**kw)


BODY = [(10, f"Body line number {n} of the regulation text") for n in range(8)]


def test_a_heading_ten_percent_larger_than_the_body_is_found_by_default(tmp_path):
    lines = BODY[:3] + [(11, "Procedure")] + BODY[3:6] + [(11, "Notes")] + BODY[6:]
    assert _headings(tmp_path, lines) == ["Procedure", "Notes"]


def test_slightly_larger_body_text_is_not_a_heading(tmp_path):
    lines = BODY[:3] + [(10.5, "Procedure")] + BODY[3:]
    assert _headings(tmp_path, lines) == []  # 5% larger: font noise, not a heading


@pytest.mark.parametrize("caption", ["Fig. 1 Application flow", "Figure 2 Limits", "Table 3 Lodging limits", "Note 1 Amounts include tax",
                                     "Source: internal document"])
def test_captions_are_not_headings(tmp_path, caption):
    lines = BODY[:3] + [(13, caption)] + BODY[3:]
    assert _headings(tmp_path, lines) == []


def test_a_bare_label_that_looks_like_a_caption_word_is_still_a_heading(tmp_path):
    lines = BODY[:3] + [(13, "Notes")] + BODY[3:]
    assert _headings(tmp_path, lines) == ["Notes"]


def test_real_headings_survive_captions_on_the_same_page(tmp_path):
    lines = BODY[:2] + [(13, "Procedure")] + BODY[2:4] + [(12, "Fig. 1 Flow")] + BODY[4:6] + [(13, "Notes")] + BODY[6:]
    assert _headings(tmp_path, lines) == ["Procedure", "Notes"]


def test_a_page_where_most_lines_are_large_has_no_headings(tmp_path):
    lines = BODY[:3] + [(14, f"Large line {n}") for n in range(8)]
    assert _headings(tmp_path, lines) == []


def test_the_guard_allows_a_page_with_a_few_headings_among_short_pages(tmp_path):
    lines = [(10, "Body line one of the text"), (10, "Body line two of the text"), (10, "Body line three of the text"),
             (14, "Heading A"), (10, "Body line four of the text"), (14, "Heading B"), (10, "Body line five of the text"),
             (10, "Body line six of the text")]
    assert _headings(tmp_path, lines) == ["Heading A", "Heading B"]


@pytest.mark.parametrize("text", ["図1 申請の流れ", "表2 宿泊費の上限", "注1 金額は税込", "出典：社内資料", "備考 本表は参考値", "写真3 現場の様子",
                                  "図１　申請の流れ", "※注 数値は概算", "Fig. 1 Flow", "Table 3 Limits", "Source: internal"])
def test_caption_lines_are_recognised(text):
    from docextract.pdf import is_caption_line

    assert is_caption_line(text)


@pytest.mark.parametrize("text", ["備考", "注意事項", "表示の方法", "図書の貸出", "出典の扱い", "Notes", "Tables and charts", "Source code"])
def test_real_headings_are_not_mistaken_for_captions(text):
    from docextract.pdf import is_caption_line

    assert not is_caption_line(text)


def test_the_caption_check_sees_the_space_that_japanese_joining_removes():
    # "備考 本表は参考値" becomes "備考本表は参考値" after join_japanese: the raw line still says it is a note
    from docextract.pdf import is_caption_line, join_japanese

    assert join_japanese("備考 本表は参考値") == "備考本表は参考値"
    assert is_caption_line("備考 本表は参考値") and not is_caption_line("備考本表は参考値")
