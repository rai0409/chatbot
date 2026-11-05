"""Title rows above a table, summary rows, FAQ auto-detection and vertical pages. Synthetic data only."""
from __future__ import annotations

import csv
import zipfile

import pytest

from rag_core.document_converters import convert_file_to_canonical_chunks as convert
from rag_core.document_converters.table_layout import find_layout


def _numbered(rows):
    return [(i + 1, list(r)) for i, r in enumerate(rows)]


HEADER = ["社員番号", "氏名", "部署", "金額", "備考"]
DATA = [["1001", "山田太郎", "営業部", "12,000", "出張費"], ["1002", "佐藤次郎", "総務部", "3,500", "会議費"]]


def test_a_plain_table_keeps_its_first_row_as_the_header():
    layout = find_layout(_numbered([HEADER] + DATA))
    assert layout.header_index == 0 and layout.title == "" and layout.notes == []


def test_title_and_note_rows_above_the_header_are_skipped_and_kept_as_context():
    rows = [["経費一覧（2024年4月分）", "", "", "", ""], ["出力日", "2024/05/01", "", "", ""], [], HEADER] + DATA
    layout = find_layout(_numbered(rows))
    assert layout.header_index == 3
    assert layout.title == "経費一覧（2024年4月分）"
    assert layout.notes == ["出力日: 2024/05/01"]


def test_a_summary_row_is_flagged_not_dropped():
    rows = [HEADER] + DATA + [["", "", "", "15,500", "合計"]]
    layout = find_layout(_numbered(rows))
    assert layout.summary_rows == {4}


def test_a_numeric_first_row_is_never_taken_for_a_title_or_header_shift():
    rows = [["1", "2", "3"], ["4", "5", "6"], ["7", "8", "9"]]
    assert find_layout(_numbered(rows)).header_index == 0


def test_a_single_column_table_is_unchanged():
    assert find_layout(_numbered([["名前"], ["山田太郎"], ["佐藤次郎"]])).header_index == 0


def _write_csv(path, rows, encoding="cp932"):
    with open(path, "w", encoding=encoding, newline="") as handle:
        csv.writer(handle).writerows(rows)
    return path


def test_csv_with_a_title_uses_the_real_header(tmp_path):
    rows = [["経費一覧（2024年4月分）", "", "", "", ""], ["出力日", "2024/05/01", "", "", ""], [], HEADER] + DATA + [["", "", "", "15,500", "合計"]]
    chunks = convert(_write_csv(tmp_path / "e.csv", rows), tenant_id="default", source_doc="e.csv")
    assert len(chunks) == 3  # two employees and the total
    first = chunks[0]
    assert first["text"].startswith("社員番号: 1001") and "氏名: 山田太郎" in first["text"]
    assert first["title"] == "経費一覧（2024年4月分）" or "経費一覧（2024年4月分）" in " ".join(first["section_path"])
    assert "経費一覧（2024年4月分）" in first["searchable_text"]
    assert chunks[-1].get("row_kind") == "summary" and chunks[0].get("row_kind") != "summary"


def test_csv_without_a_title_is_unchanged(tmp_path):
    chunks = convert(_write_csv(tmp_path / "p.csv", [HEADER] + DATA, "utf-8"), tenant_id="default", source_doc="p.csv")
    assert len(chunks) == 2 and chunks[0]["text"].startswith("社員番号: 1001")
    assert "row_kind" not in chunks[0] or chunks[0]["row_kind"] == "data"


def _xlsx(path, sheet_rows):
    W = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    cells = []
    for r, row in enumerate(sheet_rows, start=1):
        for c, value in enumerate(row):
            if value == "":
                continue
            ref = f"{chr(65 + c)}{r}"
            cells.append((r, f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>'))
    rows_xml = "".join(
        f'<row r="{r}">' + "".join(x for rr, x in cells if rr == r) + "</row>" for r in range(1, len(sheet_rows) + 1)
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{W}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="経費" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{W}"><sheetData>{rows_xml}</sheetData></worksheet>')
    return path


def test_xlsx_with_a_merged_title_uses_the_real_header(tmp_path):
    rows = [["経費明細", "", "", ""], ["日付", "区分", "金額", "備考"], ["2024-04-01", "交通費", "1200", "電車"], ["2024-04-15", "宿泊費", "12000", "出張"]]
    chunks = convert(_xlsx(tmp_path / "x.xlsx", rows), tenant_id="default", source_doc="x.xlsx")
    assert len(chunks) == 2
    assert chunks[0]["text"].startswith("日付: 2024-04-01") and "区分: 交通費" in chunks[0]["text"]
    assert "経費明細" in chunks[0]["searchable_text"]
    assert chunks[0]["cell_range"].startswith("A3")


# --- FAQ auto-detection ------------------------------------------------------------------------------------------------------------------------


def test_detect_doc_type_finds_question_answer_lists():
    from rag_core.chunking_ja import DOC_TYPE_FAQ, DOC_TYPE_POLICY, detect_doc_type

    faq = "Q1. 申請方法は\nA1. システムから\n\nQ2. 期限は\nA2. 月末です\n\nQ3. 担当は\nA3. 管理部です"
    assert detect_doc_type(faq) == DOC_TYPE_FAQ
    colon = "質問：申請方法は\n回答：システムから\n質問：期限は\n回答：月末です\n質問：担当は\n回答：管理部です"
    assert detect_doc_type(colon) == DOC_TYPE_FAQ
    policy = "第1条（目的）\nこの規程は経費の精算を定める。\n第2条（定義）\n経費とは業務上の支出をいう。"
    assert detect_doc_type(policy) == DOC_TYPE_POLICY
    assert detect_doc_type("") == DOC_TYPE_POLICY
    # two stray "Q:" lines in a long document are not a FAQ
    assert detect_doc_type("Q: 例\nA: 例\n" + "本文の一行です。\n" * 40) == DOC_TYPE_POLICY


# --- the PDF default type --------------------------------------------------------------------------------------------------------------------


def test_the_pdf_adapter_asks_for_auto_detection_when_no_type_is_given(monkeypatch, tmp_path):
    from rag_core.document_converters import pdf_adapter
    from scripts import pdf_to_canonical_jsonl

    seen = {}

    def fake(path, **kw):
        seen.update(kw)
        return [{"text": "x"}], {}

    monkeypatch.setattr(pdf_to_canonical_jsonl, "convert_pdf_to_rows", fake)
    pdf_adapter.convert_pdf_with_report(tmp_path / "a.pdf")
    assert seen["doc_type"] == "auto"
    pdf_adapter.convert_pdf_with_report(tmp_path / "a.pdf", doc_type="policy")
    assert seen["doc_type"] == "policy"


def test_auto_detection_reads_a_question_answer_pdf_as_a_faq(tmp_path):
    from rag_core import extraction
    from scripts import pdf_to_canonical_jsonl as conv

    pytest.importorskip("pdfplumber")
    extraction.load()
    from docextract.testing import simple_pdf

    lines = ["Q1. How do I apply?", "A1. Use the system.", "", "Q2. When is the deadline?", "A2. The end of the month",
             "", "Q3. Who approves it?", "A3. Your manager."]
    texts, top = [], 80
    for line in lines:
        if line:
            texts.append((60, top, 10, line))
        top += 16
    path = tmp_path / "faq.pdf"
    path.write_bytes(simple_pdf([{"texts": texts}]))
    rows, report = conv.convert_pdf_to_rows(path, doc_type="auto", ocr=False, source_doc="faq.pdf")
    assert report["doc_type"] == "faq_glossary"
    assert {r.get("faq_question") for r in rows if r.get("faq_question")} >= {"How do I apply?", "When is the deadline?", "Who approves it?"}


def test_an_explicit_type_is_never_overridden(tmp_path):
    from rag_core import extraction
    from scripts import pdf_to_canonical_jsonl as conv

    pytest.importorskip("pdfplumber")
    extraction.load()
    from docextract.testing import simple_pdf

    texts = [(60, 80 + 16 * i, 10, line) for i, line in enumerate(
        ["Q1. How do I apply?", "A1. Use the system.", "Q2. When?", "A2. Monthly.", "Q3. Who?", "A3. Manager."])]
    path = tmp_path / "faq.pdf"
    path.write_bytes(simple_pdf([{"texts": texts}]))
    _rows, report = conv.convert_pdf_to_rows(path, doc_type="policy", ocr=False, source_doc="faq.pdf")
    assert report["doc_type"] == "policy_spec"


# --- vertical pages are not tables --------------------------------------------------------------------------------------------------------


def test_a_vertical_page_is_not_split_into_table_rows():
    from rag_core import extraction

    pytest.importorskip("pdfplumber")
    extraction.load()
    from docextract import pdf as dxpdf

    class Page:  # the calls the converter makes on a vertical page
        def __init__(self):
            self.table_calls = []

        def is_vertical(self):
            return True

        def tables(self, borderless=None):
            self.table_calls.append(borderless)
            return []

    from scripts import pdf_to_canonical_jsonl as conv

    page = Page()
    assert conv._tables_of_page(page) == []
    assert page.table_calls == [False]  # only ruled tables are looked for on a vertical page
    assert dxpdf is not None
