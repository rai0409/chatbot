from __future__ import annotations

import json
import zipfile

import pytest

import config
from rag_core import retrieval
from rag_core.document_converters import convert_file_to_canonical_chunks, detect_format
from scripts.convert_document_to_canonical_jsonl import main as convert_cli_main


REQUIRED_CANONICAL_FIELDS = ("id", "text", "type", "source_doc")


def _assert_canonical(chunks):
    assert chunks
    seen_ids = set()
    for chunk in chunks:
        for field in REQUIRED_CANONICAL_FIELDS:
            assert chunk.get(field), field
        assert chunk["id"] not in seen_ids
        seen_ids.add(chunk["id"])
        assert chunk["searchable_text"]
        assert chunk["display_text"]
        assert chunk["tenant_id"]
        assert chunk["parser"]
        assert chunk["chunk_type"]
        assert chunk["source_type"]


# --- CSV ---------------------------------------------------------------------


def _write_csv(path, rows):
    path.write_text("\n".join(",".join(r) for r in rows) + "\n", encoding="utf-8")


def test_csv_rows_become_row_chunks(tmp_path):
    src = tmp_path / "products.csv"
    _write_csv(src, [["商品名", "価格"], ["りんご", "120"], ["みかん", "80"]])

    chunks = convert_file_to_canonical_chunks(src)

    _assert_canonical(chunks)
    assert len(chunks) == 2
    assert chunks[0]["chunk_type"] == "row"
    assert chunks[0]["text"] == "商品名: りんご\n価格: 120"
    assert chunks[0]["row_number"] == 2
    assert chunks[0]["columns"] == ["商品名", "価格"]
    assert chunks[0]["sheet_name"] is None
    assert chunks[0]["source_type"] == "csv"


def test_csv_qa_columns_become_qa_pairs(tmp_path):
    src = tmp_path / "faq.csv"
    _write_csv(src, [["質問", "回答", "備考"], ["営業時間は？", "9時から17時です", "本社"]])

    chunks = convert_file_to_canonical_chunks(src, tenant_id="tenant_a")

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk["chunk_type"] == "qa_pair"
    assert chunk["text"] == "Q: 営業時間は？\nA: 9時から17時です"
    # searchable_text carries both Q and A plus remaining columns.
    assert "営業時間は？" in chunk["searchable_text"]
    assert "9時から17時です" in chunk["searchable_text"]
    assert "備考: 本社" in chunk["searchable_text"]
    assert chunk["tenant_id"] == "tenant_a"


def test_csv_ids_are_deterministic(tmp_path):
    src = tmp_path / "faq.csv"
    _write_csv(src, [["Q", "A"], ["質問です", "回答です"]])

    first = convert_file_to_canonical_chunks(src)
    second = convert_file_to_canonical_chunks(src)

    assert [c["id"] for c in first] == [c["id"] for c in second]


# --- XLSX --------------------------------------------------------------------


_XLSX_CT = """<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>"""


def _write_xlsx(path, sheets, shared_strings=()):
    """sheets: list of (name, sheet_xml). Shared strings referenced as t=\"s\"."""
    workbook_sheets = "".join(
        f'<sheet name="{name}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
        for i, (name, _) in enumerate(sheets)
    )
    workbook = (
        '<?xml version="1.0"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{workbook_sheets}</sheets></workbook>"
    )
    rels = (
        '<?xml version="1.0"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(
            f'<Relationship Id="rId{i + 1}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{i + 1}.xml"/>'
            for i in range(len(sheets))
        )
        + "</Relationships>"
    )
    sst = (
        '<?xml version="1.0"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        + "".join(f"<si><t>{s}</t></si>" for s in shared_strings)
        + "</sst>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", _XLSX_CT)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", rels)
        zf.writestr("xl/sharedStrings.xml", sst)
        for i, (_, sheet_xml) in enumerate(sheets):
            zf.writestr(f"xl/worksheets/sheet{i + 1}.xml", sheet_xml)


def _sheet_xml(rows):
    """rows: list of (row_number, [(cell_ref, kind, value)]) with kind s|inline|n."""
    body = []
    for row_number, cells in rows:
        cell_xml = ""
        for ref, kind, value in cells:
            if kind == "s":
                cell_xml += f'<c r="{ref}" t="s"><v>{value}</v></c>'
            elif kind == "inline":
                cell_xml += f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>'
            else:
                cell_xml += f'<c r="{ref}"><v>{value}</v></c>'
        body.append(f'<row r="{row_number}">{cell_xml}</row>')
    return (
        '<?xml version="1.0"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(body)}</sheetData></worksheet>"
    )


def test_xlsx_multiple_sheets_with_qa_detection(tmp_path):
    src = tmp_path / "faq.xlsx"
    shared = ["質問", "回答", "返品できますか", "14日以内なら可能です"]
    faq_sheet = _sheet_xml(
        [
            (1, [("A1", "s", 0), ("B1", "s", 1)]),
            (2, [("A2", "s", 2), ("B2", "s", 3)]),
        ]
    )
    data_sheet = _sheet_xml(
        [
            (1, [("A1", "inline", "項目"), ("B1", "inline", "数量")]),
            (2, [("A2", "inline", "在庫"), ("B2", "n", "42.0")]),
        ]
    )
    _write_xlsx(src, [("FAQ", faq_sheet), ("データ", data_sheet)], shared_strings=shared)

    chunks = convert_file_to_canonical_chunks(src, tenant_id="tenant_x")

    _assert_canonical(chunks)
    assert len(chunks) == 2
    qa = chunks[0]
    assert qa["sheet_name"] == "FAQ"
    assert qa["chunk_type"] == "qa_pair"
    assert qa["text"] == "Q: 返品できますか\nA: 14日以内なら可能です"
    assert qa["row_number"] == 2
    assert qa["cell_range"] == "A2:B2"
    assert qa["tenant_id"] == "tenant_x"
    row = chunks[1]
    assert row["sheet_name"] == "データ"
    assert row["chunk_type"] == "row"
    assert "数量: 42" in row["text"]  # "42.0" trimmed to "42"


# --- DOCX --------------------------------------------------------------------


def _docx_xml(body: str) -> str:
    return (
        '<?xml version="1.0"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )


def _p(text, style=None):
    style_xml = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{style_xml}<w:r><w:t>{text}</w:t></w:r></w:p>"


def _tbl(rows):
    out = "<w:tbl>"
    for cells in rows:
        out += "<w:tr>" + "".join(f"<w:tc>{_p(c)}</w:tc>" for c in cells) + "</w:tr>"
    return out + "</w:tbl>"


def _write_docx(path, body_xml):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", _docx_xml(body_xml))


def test_docx_headings_paragraphs_and_tables(tmp_path):
    src = tmp_path / "manual.docx"
    body = (
        _p("第一章 運用手順", style="Heading1")
        + _p("毎朝バックアップを確認します。")
        + _p("FAQ一覧", style="Heading1")
        + _tbl([["質問", "回答"], ["パスワードを忘れた場合は？", "管理者に連絡してください"]])
    )
    _write_docx(src, body)

    chunks = convert_file_to_canonical_chunks(src)

    _assert_canonical(chunks)
    para = chunks[0]
    assert para["chunk_type"] == "paragraph"
    assert para["text"] == "毎朝バックアップを確認します。"
    assert para["heading_path"] == ["第一章 運用手順"]
    assert para["paragraph_index"] == 2
    qa = chunks[1]
    assert qa["chunk_type"] == "qa_pair"
    assert qa["text"] == "Q: パスワードを忘れた場合は？\nA: 管理者に連絡してください"
    assert qa["heading_path"] == ["FAQ一覧"]
    assert qa["table_index"] == 1


def test_docx_plain_table_rows_become_table_row_chunks(tmp_path):
    src = tmp_path / "list.docx"
    _write_docx(src, _tbl([["項目", "値"], ["担当", "佐藤"]]))

    chunks = convert_file_to_canonical_chunks(src)

    assert len(chunks) == 1
    assert chunks[0]["chunk_type"] == "table_row"
    assert chunks[0]["text"] == "項目: 担当\n値: 佐藤"


# --- PPTX --------------------------------------------------------------------


def _pptx_slide(title=None, body_lines=(), table_rows=None):
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    shapes = ""
    if title:
        shapes += (
            "<p:sp><p:nvSpPr><p:nvPr><p:ph type=\"title\"/></p:nvPr></p:nvSpPr>"
            f"<p:txBody><a:p><a:r><a:t>{title}</a:t></a:r></a:p></p:txBody></p:sp>"
        )
    if body_lines:
        paragraphs = "".join(f"<a:p><a:r><a:t>{line}</a:t></a:r></a:p>" for line in body_lines)
        shapes += f"<p:sp><p:txBody>{paragraphs}</p:txBody></p:sp>"
    table = ""
    if table_rows:
        rows_xml = ""
        for cells in table_rows:
            rows_xml += (
                "<a:tr>"
                + "".join(
                    f"<a:tc><a:txBody><a:p><a:r><a:t>{c}</a:t></a:r></a:p></a:txBody></a:tc>"
                    for c in cells
                )
                + "</a:tr>"
            )
        table = f"<p:graphicFrame><a:tbl>{rows_xml}</a:tbl></p:graphicFrame>"
    return (
        '<?xml version="1.0"?>'
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        f'xmlns:a="{a}"><p:cSld><p:spTree>{shapes}{table}</p:spTree></p:cSld></p:sld>'
    )


def _write_pptx(path, slides):
    with zipfile.ZipFile(path, "w") as zf:
        for i, slide_xml in enumerate(slides, start=1):
            zf.writestr(f"ppt/slides/slide{i}.xml", slide_xml)


def test_pptx_slides_and_tables(tmp_path):
    src = tmp_path / "deck.pptx"
    _write_pptx(
        src,
        [
            _pptx_slide(title="運用概要", body_lines=["毎月の定例会で報告します。"]),
            _pptx_slide(table_rows=[["項目", "値"], ["稼働率", "99.9%"]]),
        ],
    )

    chunks = convert_file_to_canonical_chunks(src)

    _assert_canonical(chunks)
    slide = chunks[0]
    assert slide["chunk_type"] == "slide"
    assert slide["slide_number"] == 1
    assert slide["slide_title"] == "運用概要"
    assert "毎月の定例会で報告します。" in slide["text"]
    table_row = chunks[1]
    assert table_row["chunk_type"] == "table_row"
    assert table_row["slide_number"] == 2
    assert table_row["text"] == "項目: 稼働率\n値: 99.9%"


# --- PDF adapter -------------------------------------------------------------


def test_pdf_adapter_wraps_existing_converter(tmp_path):
    fitz = pytest.importorskip("fitz")
    src = tmp_path / "sample.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (72, 72),
        "Operations manual. Backup verification runs every morning at 06:00 JST.",
    )
    doc.save(str(src))
    doc.close()

    chunks = convert_file_to_canonical_chunks(src, tenant_id="tenant_p")

    assert chunks
    for chunk in chunks:
        assert chunk["source_type"] == "pdf"
        assert chunk["parser"] == "pdf_to_canonical_jsonl"
        assert chunk.get("tenant_id") == "tenant_p"
        assert chunk.get("source_pages")


# --- common ------------------------------------------------------------------


def test_unsupported_extension_fails_clearly(tmp_path):
    src = tmp_path / "notes.txt"
    src.write_text("hello", encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported document format"):
        convert_file_to_canonical_chunks(src)
    with pytest.raises(ValueError, match="supported"):
        detect_format(src)


def test_cli_writes_valid_jsonl_with_summary(tmp_path, capsys):
    src = tmp_path / "faq.csv"
    _write_csv(src, [["質問", "回答"], ["営業時間は？", "9時から17時です"]])
    out = tmp_path / "out.jsonl"

    exit_code = convert_cli_main(
        ["--input", str(src), "--output", str(out), "--tenant-id", "tenant_cli"]
    )

    assert exit_code == 0
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == "tenant_cli"
    summary = capsys.readouterr().out
    assert "chunks=1" in summary
    assert "csv/qa_pair=1" in summary


def test_converted_chunks_flow_through_keyword_retrieval(monkeypatch, tmp_path):
    src = tmp_path / "faq.csv"
    _write_csv(src, [["質問", "回答"], ["返金はできますか？", "クーポン利用分は返金対象外です"]])
    out = tmp_path / "chunks.jsonl"
    convert_cli_main(["--input", str(src), "--output", str(out)])

    monkeypatch.setattr(config, "CHUNKS_JSONL_PATH", str(out))
    retrieval._INDEX_CACHE.update({"path": None, "mtime": None, "index": None})
    retrieval._KEYWORD_INDEX_WARNED_KEYS.clear()

    # クーポン appears only in the answer column: a hit proves Q and A are
    # searched together, through the real keyword index.
    hits = retrieval.keyword_retrieve("クーポン の返金", top_k=3)

    assert hits
    assert hits[0].metadata["chunk_type"] == "qa_pair"
    assert hits[0].metadata["source_type"] == "csv"
