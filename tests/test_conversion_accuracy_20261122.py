"""Conversion-accuracy harness (eval/conversion) and the converter fixes it measures.

Fixtures: tests/fixtures/conversion (tools/make_dummy_docs.py --conversion-fixtures; synthetic, fixed example values).
Scratch files are isolated using pytest's temporary directory factory.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.conversion import harness, metrics as M
from eval.conversion.__main__ import main as conversion_main
from rag_core.document_converters import convert_file_to_canonical_chunks as convert
from rag_core.document_converters.csv_converter import detect_encoding
from rag_core.document_converters.xlsx_converter import _classify_format_code, format_era_date, format_number
from tools import make_dummy_docs as D

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "conversion"
THRESHOLDS = ROOT / "eval" / "conversion" / "thresholds_v1.json"


@pytest.fixture(scope="module")
def scratch(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("conversion_accuracy")


@pytest.fixture(scope="module")
def result():
    return harness.run(FIXTURES)


# --- the committed fixtures and the thresholds --------------------------------------------------------------------


def test_committed_fixtures_are_reproducible(scratch):
    out = scratch / "fixtures_regenerated"
    D.make_conversion_fixtures(out)
    committed = sorted(p.name for p in FIXTURES.iterdir() if not p.name.startswith("."))
    assert sorted(p.name for p in out.iterdir()) == committed
    for name in committed:
        assert (out / name).read_bytes() == (FIXTURES / name).read_bytes(), name


def test_every_fixture_meets_the_thresholds(result):
    thresholds = json.loads(THRESHOLDS.read_text(encoding="utf-8"))
    assert harness.check_thresholds(result, thresholds) == []
    assert result["aggregate"]["overall"]["errors"] == 0


def test_run_records_provenance(result):
    assert result["schema"] == "conversion-run-v1"
    assert len(result["fixtures_sha256"]) == 64
    assert set(result["fixture_files"]) >= {"deck.pptx", "deck.pptx.truth.json", "policy.docx", "book.xlsx"}
    assert "pptx_converter.py" in result["converter_versions"]["modules"]
    formats = {d["format"] for d in result["documents"]}
    assert formats == {"csv", "xlsx", "docx", "pptx"}


def test_cli_run_and_compare(scratch, capsys):
    out = scratch / "run.json"
    assert conversion_main(["run", "--fixtures", str(FIXTURES), "--out", str(out), "--thresholds", str(THRESHOLDS)]) == 0
    run = json.loads(out.read_text(encoding="utf-8"))
    worse = json.loads(json.dumps(run))
    worse["documents"][0]["metrics"]["table"]["f1"] = 0.5
    bad = scratch / "worse.json"
    bad.write_text(json.dumps(worse), encoding="utf-8")
    assert conversion_main(["compare", "--base", str(out), "--candidate", str(bad), "--fail-on-regression"]) == 1
    assert "REGRESSED" in capsys.readouterr().out


# --- metrics --------------------------------------------------------------------------------------------------------------


def test_kendall_tau_and_reading_order():
    assert M.kendall_tau([0, 1, 2, 3]) == 1.0
    assert M.kendall_tau([3, 2, 1, 0]) == -1.0
    scores = M.reading_order(["第一", "第二", "第三"], "第三\n第一\n第二")
    assert scores["blocks_found"] == 3 and scores["kendall_tau"] == pytest.approx(-1 / 3, abs=1e-5)


def test_text_scores_cer():
    s = M.text_scores("経費精算規程", "経費清算規程")
    assert s["edit_distance"] == 1 and s["cer"] == pytest.approx(1 / 6, abs=1e-5)


def test_merged_cells_expand_and_two_header_rows_combine():
    table = {"cells": [["区分", "上限額", ""], ["", "国内", "海外"], ["宿泊費", "12,000円", "20,000円"]],
             "merged": [{"row": 0, "col": 0, "rowspan": 2}, {"row": 0, "col": 1, "colspan": 2}], "header_rows": 2}
    assert M.expected_table_pairs(table) == [("区分", "宿泊費"), ("上限額 国内", "12,000円"), ("上限額 海外", "20,000円")]


def test_table_scores_separate_wrong_header_from_lost_value():
    table = {"cells": [["区分", "金額"], ["日当", "3,000円"]]}
    chunk = {"chunk_type": "table_row", "text": "区分: 日当\n値: 3,000円"}
    scores = M.table_scores([table], [chunk])
    assert scores["f1"] == 0.5 and scores["value_only_f1"] == 1.0


def test_leakage_counts_body_chunks():
    body = [{"text": "株式会社サンプル 社内規程集 第1条"}, {"text": "本文"}]
    assert M.leakage(["株式会社サンプル 社内規程集"], body)["rate"] == 0.5


# --- converter fixes -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("code, serial, shown", [
    ('[$-ja-JP]ggge"年"m"月"d"日"', "45383", "令和6年4月1日"),
    ('[$-411]ggge"年"m"月"d"日"', "43190", "平成30年3月31日"),
    ("[$-411]ge.m.d", "45397", "R6.4.15"),
    ('[$-411]ggg"元年"', "43586", "令和元年"),  # literal text is kept as written
])
def test_era_date_formats_render_like_excel(code, serial, shown):
    assert format_number(serial, _classify_format_code(code)) == shown


def test_ordinary_date_formats_are_unchanged():
    assert _classify_format_code('yyyy"年"m"月"d"日"') == "date"
    assert _classify_format_code("0.00E+00") == ""


def test_era_boundaries():
    from datetime import date

    assert format_era_date(date(2019, 4, 30), "ggge") == "平成31"
    assert format_era_date(date(2019, 5, 1), "ggge") == "令和1"
    assert format_era_date(date(1989, 1, 7), "gge") == "昭64"


def test_xlsx_furigana_is_not_text_and_uncalculated_formula_warns():
    chunks = convert(FIXTURES / "book.xlsx")
    blob = "\n".join(c["text"] + c["searchable_text"] for c in chunks)
    assert "山田太郎" in blob and "ヤマダ" not in blob
    warned = [c for c in chunks if c.get("conversion_warnings")]
    assert len(warned) == 1 and warned[0]["conversion_warnings"][0] == {
        "code": "formula_without_cached_value", "sheet": "経費", "cell": "E4"}
    assert "交通費" in chunks[0]["text"]  # rich-text runs are joined


@pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be"])
def test_utf16_without_bom_is_detected(encoding):
    text = "社員番号,氏名\r\n1001,山田太郎\r\n"
    assert detect_encoding(text.encode(encoding)) == encoding


@pytest.mark.parametrize("encoding, expected", [("utf-8", "utf-8"), ("cp932", "cp932")])
def test_other_encodings_are_not_taken_for_utf16(encoding, expected):
    assert detect_encoding("社員番号,氏名\r\n1001,山田太郎\r\n".encode(encoding)) == expected


def _docx(scratch: Path, name: str, body: str) -> Path:
    path = scratch / name
    D._write_zip(path, {"word/document.xml": f'<w:document xmlns:w="{D._NS_W}"><w:body>{body}</w:body></w:document>'})
    return path


def test_docx_rows_marked_as_header_rows_form_one_header(scratch):
    hdr = "<w:trPr><w:tblHeader/></w:trPr>"
    row = lambda cells, pr="": "<w:tr>" + pr + "".join(D._w_tc(c) for c in cells) + "</w:tr>"  # noqa: E731
    body = "<w:tbl>" + row(["区分", "金額"], hdr) + row(["", "税込"], hdr) + row(["日当", "3,300円"]) + "</w:tbl>"
    chunks = convert(_docx(scratch, "two_header_rows.docx", body))
    assert [c["text"] for c in chunks] == ["区分: 日当\n金額 税込: 3,300円"]


def test_docx_full_width_first_row_is_the_table_title(scratch):
    span = '<w:gridSpan w:val="2"/>'
    body = ("<w:tbl><w:tr>" + D._w_tc("別表 日当", span) + "</w:tr>"
            "<w:tr>" + D._w_tc("区分") + D._w_tc("金額") + "</w:tr>"
            "<w:tr>" + D._w_tc("日当") + D._w_tc("3,000円") + "</w:tr></w:tbl>")
    chunks = convert(_docx(scratch, "title_row.docx", body))
    assert chunks[0]["text"] == "区分: 日当\n金額: 3,000円"
    assert chunks[0]["section_path"] == ["別表 日当"]


def test_docx_plain_table_keeps_first_row_header(scratch):
    body = "<w:tbl>" + "".join("<w:tr>" + "".join(D._w_tc(c) for c in r) + "</w:tr>" for r in [["項目", "値"], ["担当", "佐藤次郎"]]) + "</w:tbl>"
    assert convert(_docx(scratch, "plain.docx", body))[0]["text"] == "項目: 担当\n値: 佐藤次郎"


def test_pptx_reads_by_position_through_groups_and_charts():
    slide = convert(FIXTURES / "deck.pptx")[0]
    lines = slide["text"].splitlines()
    assert lines[:6] == ["四半期報告", "売上は前年比10%増加した。", "費用は横ばいだった。", "第一四半期: 1,200万円",
                         "第二四半期: 1,500万円", "まとめ: 次回は7月に報告します。"]
    second = [c for c in convert(FIXTURES / "deck.pptx") if c.get("slide_number") == 2 and c["chunk_type"] == "slide"][0]
    assert "グラフ: 月別の推移" in second["text"] and "系列: 売上高" in second["text"] and "項目: 4月 5月" in second["text"]


def test_pptx_nested_group_offsets_accumulate(scratch):
    emu = D._EMU
    inner = (f'<p:grpSp><p:grpSpPr><a:xfrm><a:off x="0" y="{2 * emu}"/><a:ext cx="{emu}" cy="{emu}"/>'
             f'<a:chOff x="0" y="0"/><a:chExt cx="{emu}" cy="{emu}"/></a:xfrm></p:grpSpPr>'
             + D._p_sp(3, ["下の段"], 0, 0) + "</p:grpSp>")
    outer = (f'<p:grpSp><p:grpSpPr><a:xfrm><a:off x="0" y="{emu}"/><a:ext cx="{emu}" cy="{4 * emu}"/>'
             f'<a:chOff x="0" y="0"/><a:chExt cx="{emu}" cy="{4 * emu}"/></a:xfrm></p:grpSpPr>'
             + inner + "</p:grpSp>")
    path = scratch / "nested.pptx"
    D._write_zip(path, {"ppt/slides/slide1.xml": D._p_slide(outer + D._p_sp(2, ["上の段"], 0, 2 * emu))})
    assert convert(path)[0]["text"].splitlines() == ["上の段", "下の段"]  # inner group sits at 1 + 2 inch


def test_pptx_smartart_text(scratch):
    dgm = "http://schemas.openxmlformats.org/drawingml/2006/diagram"
    frame = ('<p:graphicFrame><p:xfrm><a:off x="0" y="0"/><a:ext cx="1" cy="1"/></p:xfrm><a:graphic>'
             f'<a:graphicData uri="{dgm}"><dgm:relIds xmlns:dgm="{dgm}" r:dm="rId5"/></a:graphicData></a:graphic></p:graphicFrame>')
    data = (f'<dgm:dataModel xmlns:dgm="{dgm}" xmlns:a="{D._NS_A}"><dgm:ptLst>'
            '<dgm:pt modelId="1"><dgm:t><a:p><a:r><a:t>申請</a:t></a:r></a:p></dgm:t></dgm:pt>'
            '<dgm:pt modelId="2"><dgm:t><a:p><a:r><a:t>承認</a:t></a:r></a:p></dgm:t></dgm:pt></dgm:ptLst></dgm:dataModel>')
    path = scratch / "smartart.pptx"
    D._write_zip(path, {
        "ppt/slides/slide1.xml": D._p_slide(frame),
        "ppt/slides/_rels/slide1.xml.rels": D._rels([("rId5", "diagramData", "../diagrams/data1.xml")]),
        "ppt/diagrams/data1.xml": data,
    })
    assert convert(path)[0]["text"] == "申請\n承認"
