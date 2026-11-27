"""Investigation: build DUMMY Japanese business documents with known ground truth.

Purpose : test the extraction / preprocessing pipeline on realistic inputs without real documents.
          Creates text PDFs (a regulation with headers/footers, wrapped lines, circled steps, a ruled table and
          compatibility characters; a Q&A list in three layouts; a vertical-writing page), CSV files in the
          encodings Japanese offices use, and an Excel file with merged cells, dates and a hidden sheet.
Usage   : .venv/bin/python tools/make_dummy_docs.py [--out data/external/dummy_docs]
          .venv/bin/python tools/make_dummy_docs.py --conversion-fixtures [tests/fixtures/conversion]
Needs   : reportlab, openpyxl, and a Japanese font (the IPAex Gothic bundled in the `japanize-matplotlib` package:
          pip install reportlab openpyxl japanize-matplotlib). No network, no real data.
          --conversion-fixtures needs nothing but the standard library (the Office files are written as raw
          OOXML so every edge case is exact and the bytes are reproducible).
Output  : the documents and truth.json (what each document says) inside the repository (data/external/, git-ignored).
          --conversion-fixtures: tiny synthetic docx/pptx/xlsx/csv files plus one <name>.truth.json each
          (schema conversion-truth-v1, scored by `python -m eval.conversion run`); these ARE committed
          (tests/fixtures/conversion/, fixed example values only).
Cleanup : move data/external/dummy_docs to 即削除/ when no longer needed.
Every name, company and number below is invented (山田太郎, 株式会社サンプル, ...).
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# --- the ground truth of the regulation ------------------------------------------------------------------------------------

TITLE = "経費精算規程"
HEADER = "株式会社サンプル 社内規程集"
ARTICLES = [
    ("第1条（目的）",
     "この規程は、株式会社サンプルの従業員が業務のために支出した費用の精算に関する手続きを定め、"
     "経費の適正な管理を行うことを目的とする。"),
    ("第2条（申請期限）",
     "経費の精算は、支出した日から10日以内に所定の様式により申請しなければならない。"
     "ただし、月末に支出したものは、翌月5営業日以内に申請することができる。"),
    ("第3条（交通費）",
     "電車、バス等の公共交通機関を利用した場合の交通費は、最も経済的な経路による実費とする。"
     "タクシーを利用できるのは、午後10時以降の帰宅時に限る。"),
    ("第4条（施行）",
     "この規程は、令和6年4月1日から施行し、2024年度末まで適用する。重量が10㎏を超える荷物の"
     "発送は、温度5℃以下で管理する。第Ⅲ章に定める手順に従うこと。"),
]
STEPS = ["申請書をシステムから作成する", "領収書の画像を添付する", "上長の承認を受ける"]
BULLETS = ["提出期限を守ること", "領収書を添付すること", "私的な支出を含めないこと"]
TABLE = [["区分", "上限額", "備考"], ["宿泊費", "12,000円", "1泊あたり"], ["日当", "3,000円", "出張1日あたり"], ["会議費", "5,000円", "1人あたり"]]
QA = [
    ("経費精算の申請方法を教えてください。", "申請はシステムの経費メニューから行います。領収書の画像を添付してください。"),
    ("領収書をなくした場合はどうすればよいですか。", "再発行を依頼するか、支払証明書を作成して上長の承認を受けてください。"),
    ("海外出張の日当はいくらですか。", "地域区分により異なります。詳細は別表を確認してください。"),
]
VERTICAL = ["経費の精算は", "支出した日から", "十日以内に申請", "すること。"]

CSV_ROWS = [
    ["社員番号", "氏名", "部署", "金額", "備考"],
    ["1001", "山田太郎", "営業部", "12,000", "出張費, 宿泊"],
    ["1002", "佐藤次郎", "総務部", "3,500", "会議の\n弁当代"],
    ["1003", "田中太郎", "開発部", "", "未申請"],
]


def _wrap(text: str, width: int) -> list[str]:
    return [text[i:i + width] for i in range(0, len(text), width)]


def make_pdfs(out: Path, font_path: Path) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas
    from reportlab.platypus import Table, TableStyle
    from reportlab.lib import colors

    pdfmetrics.registerFont(TTFont("IPAex", str(font_path)))
    width, height = A4
    left, wrap_at = 60, 34  # characters per line, like a layout-wrapped PDF

    def header_footer(c, page, pages):
        c.setFont("IPAex", 9)
        c.drawString(left, height - 36, HEADER)
        c.drawCentredString(width / 2, 30, f"- {page} -")

    # --- regulation: 3 pages -------------------------------------------------------------------------------------------
    c = canvas.Canvas(str(out / "regulation.pdf"), pagesize=A4)
    y = height - 80
    header_footer(c, 1, 3)
    c.setFont("IPAex", 16); c.drawString(left, y, TITLE); y -= 34
    for heading, body in ARTICLES[:2]:
        c.setFont("IPAex", 12); c.drawString(left, y, heading); y -= 20
        c.setFont("IPAex", 10.5)
        for line in _wrap(body, wrap_at):
            c.drawString(left, y, line); y -= 16
        y -= 10
    c.showPage()
    header_footer(c, 2, 3)
    y = height - 80
    for heading, body in ARTICLES[2:]:
        c.setFont("IPAex", 12); c.drawString(left, y, heading); y -= 20
        c.setFont("IPAex", 10.5)
        for line in _wrap(body, wrap_at):
            c.drawString(left, y, line); y -= 16
        y -= 10
    c.setFont("IPAex", 12); c.drawString(left, y, "申請の手順"); y -= 20
    c.setFont("IPAex", 10.5)
    for mark, step in zip("①②③", STEPS):
        c.drawString(left, y, f"{mark} {step}"); y -= 16
    y -= 8
    c.setFont("IPAex", 12); c.drawString(left, y, "注意事項"); y -= 20
    c.setFont("IPAex", 10.5)
    for item in BULLETS:
        c.drawString(left, y, f"・{item}"); y -= 16
    c.showPage()
    header_footer(c, 3, 3)
    y = height - 80
    c.setFont("IPAex", 12); c.drawString(left, y, "別表 宿泊費・日当の上限"); y -= 20
    table = Table(TABLE, colWidths=[90, 90, 140])
    table.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "IPAex", 10.5), ("GRID", (0, 0), (-1, -1), 0.6, colors.black)]))
    w, h = table.wrapOn(c, width, height)
    table.drawOn(c, left, y - h)
    c.showPage()
    c.save()

    # --- Q&A list: three layouts, wrapped -------------------------------------------------------------------------------
    c = canvas.Canvas(str(out / "faq.pdf"), pagesize=A4)
    y = height - 80
    header_footer(c, 1, 1)
    layouts = [
        lambda i: (f"Q{i}. ", f"A{i}. "),
        lambda i: (f"質問{i}：", "回答："),
        lambda i: ("Q: ", "A: "),
    ]
    for index, (question, answer) in enumerate(QA, start=1):
        q_mark, a_mark = layouts[index - 1](index)
        c.setFont("IPAex", 10.5)
        for n, line in enumerate(_wrap(q_mark + question, 26)):
            c.drawString(left, y, line); y -= 16
        for n, line in enumerate(_wrap(a_mark + answer, 26)):
            c.drawString(left, y, line); y -= 16
        y -= 14
    c.showPage(); c.save()

    # --- vertical writing: columns from the right, characters stacked --------------------------------------------------
    c = canvas.Canvas(str(out / "vertical.pdf"), pagesize=A4)
    c.setFont("IPAex", 14)
    x = width - 120
    for column in VERTICAL:
        y = height - 120
        for ch in column:
            c.drawString(x, y, ch); y -= 18
        x -= 30
    c.showPage(); c.save()


def make_csvs(out: Path) -> None:
    for name, encoding in (("utf8.csv", "utf-8"), ("utf8_bom.csv", "utf-8-sig"), ("cp932.csv", "cp932"), ("utf16.csv", "utf-16")):
        with open(out / name, "w", encoding=encoding, newline="") as handle:
            csv.writer(handle).writerows(CSV_ROWS)
    # a typical export: title lines above the header, a trailing total, a blank row, and ";" as the delimiter
    with open(out / "export_with_title.csv", "w", encoding="cp932", newline="") as handle:
        w = csv.writer(handle)
        w.writerow(["経費一覧（2024年4月分）", "", "", "", ""])
        w.writerow(["出力日", "2024/05/01", "", "", ""])
        w.writerow([])
        w.writerows(CSV_ROWS)
        w.writerow(["", "", "", "16,500", "合計"])


def make_xlsx(out: Path) -> None:
    import datetime as dt

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "経費"
    ws.merge_cells("A1:C1")
    ws["A1"] = "経費明細"
    ws.append(["日付", "区分", "金額", "率"])
    ws.append([dt.date(2024, 4, 1), "交通費", 1200, 0.1])
    ws.append([dt.date(2024, 4, 15), "宿泊費", 12000, 0.08])
    ws["C3"].number_format = '#,##0"円"'
    ws["D3"].number_format = "0.0%"
    ws["A3"].number_format = "yyyy/m/d"
    ws["A4"].number_format = "yyyy/m/d"
    hidden = wb.create_sheet("内部メモ")
    hidden["A1"] = "非公開の作業メモ"
    hidden.sheet_state = "hidden"
    wb.save(out / "expenses.xlsx")


# --- conversion-accuracy fixtures (stdlib only, raw OOXML) ---------------------------------------------------------------
# Each document gets <name>.truth.json (schema conversion-truth-v1, see eval/conversion/harness.py): what a reader sees.

_FIXED_ZIP_TIME = (2024, 1, 1, 0, 0, 0)
_XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
_NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_NS_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
_NS_S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"


def _write_zip(path: Path, members: dict) -> None:
    """Reproducible zip: fixed timestamps, sorted members."""
    import zipfile

    with zipfile.ZipFile(path, "w") as zf:
        for name in sorted(members):
            info = zipfile.ZipInfo(name, date_time=_FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, members[name].encode("utf-8"))


def _rels(entries) -> str:
    body = "".join(f'<Relationship Id="{rid}" Type="{_REL}{kind}" Target="{target}"/>' for rid, kind, target in entries)
    return f'{_XML}<Relationships xmlns="{_NS_PKG}">{body}</Relationships>'


def _content_types(overrides) -> str:
    body = "".join(f'<Override PartName="/{part}" ContentType="{ctype}"/>' for part, ctype in overrides)
    return (f'{_XML}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            f'<Default Extension="xml" ContentType="application/xml"/>{body}</Types>')


def _truth(out: Path, document: str, fmt: str, **fields) -> None:
    payload = {"schema": "conversion-truth-v1", "document": document, "format": fmt, **fields}
    (out / f"{document}.truth.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


# --- docx: headings, a 2-row header made of merged cells, a tblHeader table, page header/footer -----------------------------

DOCX_HEADER = "株式会社サンプル 社内規程集"
DOCX_FOOTER = "社外秘"
DOCX_LIMITS = [["区分", "上限額", ""], ["", "国内", "海外"], ["宿泊費", "12,000円", "20,000円"], ["日当", "3,000円", "5,000円"]]
DOCX_CLAIMS = [["申請日", "金額", "担当"], ["2024/04/01", "1,200円", "山田太郎"], ["2024/04/15", "3,500円", "佐藤次郎"]]


def _w_p(text: str, style: str = "") -> str:
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{ppr}<w:r><w:t>{text}</w:t></w:r></w:p>"


def _w_tc(text: str, props: str = "") -> str:
    return f"<w:tc><w:tcPr>{props}</w:tcPr>{_w_p(text)}</w:tc>"


def make_conversion_docx(out: Path) -> None:
    v_restart, span2 = '<w:vMerge w:val="restart"/>', '<w:gridSpan w:val="2"/>'
    limits = (
        "<w:tbl>"
        f'<w:tr>{_w_tc("区分", v_restart)}{_w_tc("上限額", span2)}</w:tr>'
        f'<w:tr>{_w_tc("", "<w:vMerge/>")}{_w_tc("国内")}{_w_tc("海外")}</w:tr>'
        + "".join("<w:tr>" + "".join(_w_tc(c) for c in row) + "</w:tr>" for row in DOCX_LIMITS[2:])
        + "</w:tbl>"
    )
    claims = (
        "<w:tbl>"
        + "<w:tr><w:trPr><w:tblHeader/></w:trPr>" + "".join(_w_tc(c) for c in DOCX_CLAIMS[0]) + "</w:tr>"
        + "".join("<w:tr>" + "".join(_w_tc(c) for c in row) + "</w:tr>" for row in DOCX_CLAIMS[1:])
        + "</w:tbl>"
    )
    body = (
        _w_p("経費精算規程", "Heading1")
        + _w_p("この規程は、株式会社サンプルの経費精算の手続きを定める。")
        + _w_p("第1条（申請期限）", "Heading2")
        + _w_p("経費の精算は、支出した日から10日以内に申請する。")
        + _w_p("別表 宿泊費の上限", "Heading2")
        + limits
        + _w_p("第2条（承認）", "Heading2")
        + _w_p("申請は上長の承認を受けなければならない。")
        + claims
        + '<w:sectPr><w:headerReference w:type="default" r:id="rId2"/><w:footerReference w:type="default" r:id="rId3"/></w:sectPr>'
    )
    members = {
        "[Content_Types].xml": _content_types([
            ("word/document.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"),
            ("word/header1.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"),
            ("word/footer1.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"),
        ]),
        "_rels/.rels": _rels([("rId1", "officeDocument", "word/document.xml")]),
        "word/_rels/document.xml.rels": _rels([("rId2", "header", "header1.xml"), ("rId3", "footer", "footer1.xml")]),
        "word/document.xml": f'{_XML}<w:document xmlns:w="{_NS_W}" xmlns:r="{_NS_R}"><w:body>{body}</w:body></w:document>',
        "word/header1.xml": f'{_XML}<w:hdr xmlns:w="{_NS_W}">{_w_p(DOCX_HEADER)}</w:hdr>',
        "word/footer1.xml": f'{_XML}<w:ftr xmlns:w="{_NS_W}">{_w_p(DOCX_FOOTER)}</w:ftr>',
    }
    _write_zip(out / "policy.docx", members)
    _truth(
        out, "policy.docx", "docx",
        text_blocks=["経費精算規程", "この規程は、株式会社サンプルの経費精算の手続きを定める。", "第1条（申請期限）",
                     "経費の精算は、支出した日から10日以内に申請する。", "別表 宿泊費の上限", "第2条（承認）",
                     "申請は上長の承認を受けなければならない。"],
        headings=["経費精算規程", "第1条（申請期限）", "別表 宿泊費の上限", "第2条（承認）"],
        tables=[
            {"cells": DOCX_LIMITS, "merged": [{"row": 0, "col": 0, "rowspan": 2, "colspan": 1},
                                              {"row": 0, "col": 1, "rowspan": 1, "colspan": 2}], "header_rows": 2},
            {"cells": DOCX_CLAIMS, "header_rows": 1},
        ],
        typed_cells=[
            {"header": "上限額 国内", "value": "12,000円", "kind": "number"},
            {"header": "上限額 海外", "value": "5,000円", "kind": "number"},
            {"header": "申請日", "value": "2024/04/01", "kind": "date"},
            {"header": "金額", "value": "3,500円", "kind": "number"},
        ],
        header_footer=[DOCX_HEADER, DOCX_FOOTER],
    )


# --- pptx: shapes out of XML order, a group with its own offset, notes, a table and a chart ----------------------------------

_EMU = 914400  # one inch


def _a_paragraphs(lines) -> str:
    return "".join(f"<a:p><a:r><a:t>{line}</a:t></a:r></a:p>" for line in lines)


def _p_sp(shape_id: int, lines, x=None, y=None, ph: str = "") -> str:
    nv = f'<p:nvSpPr><p:cNvPr id="{shape_id}" name="s{shape_id}"/><p:cNvSpPr/><p:nvPr>{f"<p:ph type={chr(34)}{ph}{chr(34)}/>" if ph else ""}</p:nvPr></p:nvSpPr>'
    xfrm = "" if x is None else f'<a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{3 * _EMU}" cy="{_EMU // 2}"/></a:xfrm>'
    return f"<p:sp>{nv}<p:spPr>{xfrm}</p:spPr><p:txBody><a:bodyPr/>{_a_paragraphs(lines)}</p:txBody></p:sp>"


def _p_slide(shapes: str) -> str:
    return (f'{_XML}<p:sld xmlns:p="{_NS_P}" xmlns:a="{_NS_A}" xmlns:r="{_NS_R}" xmlns:c="{_NS_C}">'
            f"<p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id=\"1\" name=\"\"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>"
            f"{shapes}</p:spTree></p:cSld></p:sld>")


PPTX_TABLE = [["項目", "金額"], ["人件費", "800万円"], ["外注費", "300万円"]]


def make_conversion_pptx(out: Path) -> None:
    # Slide 1 in XML order: title (position only in the layout), the closing line (bottom), a group (middle; its
    # children use child coordinates starting at 0, so without the group transform they would sort first), the right
    # column, the left column (both at the top).
    group = (
        '<p:grpSp><p:nvGrpSpPr><p:cNvPr id="10" name="g"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        f'<p:grpSpPr><a:xfrm><a:off x="{_EMU // 2}" y="{3 * _EMU}"/><a:ext cx="{3 * _EMU}" cy="{_EMU}"/>'
        f'<a:chOff x="0" y="0"/><a:chExt cx="{3 * _EMU}" cy="{_EMU}"/></a:xfrm></p:grpSpPr>'
        + _p_sp(12, ["第二四半期: 1,500万円"], 0, _EMU // 2)
        + _p_sp(11, ["第一四半期: 1,200万円"], 0, 0)
        + "</p:grpSp>"
    )
    slide1 = _p_slide(
        _p_sp(2, ["四半期報告"], ph="title")
        + _p_sp(3, ["まとめ: 次回は7月に報告します。"], _EMU // 2, 6 * _EMU)
        + group
        + _p_sp(5, ["費用は横ばいだった。"], 5 * _EMU, int(1.5 * _EMU) + 20000)  # same row as the left box, 2 mm lower
        + _p_sp(4, ["売上は前年比10%増加した。"], _EMU // 2, int(1.5 * _EMU))
    )
    rows = "".join(
        "<a:tr h=\"370840\">" + "".join(f"<a:tc><a:txBody><a:bodyPr/>{_a_paragraphs([c])}</a:txBody></a:tc>" for c in row) + "</a:tr>"
        for row in PPTX_TABLE
    )
    table = (
        '<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="4" name="t"/><p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr>'
        f'<p:xfrm><a:off x="{_EMU // 2}" y="{int(1.5 * _EMU)}"/><a:ext cx="{4 * _EMU}" cy="{_EMU}"/></p:xfrm>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table">'
        f"<a:tbl><a:tblGrid><a:gridCol w=\"{2 * _EMU}\"/><a:gridCol w=\"{2 * _EMU}\"/></a:tblGrid>{rows}</a:tbl>"
        "</a:graphicData></a:graphic></p:graphicFrame>"
    )
    chart_frame = (
        '<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="5" name="c"/><p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr>'
        f'<p:xfrm><a:off x="{_EMU // 2}" y="{4 * _EMU}"/><a:ext cx="{4 * _EMU}" cy="{2 * _EMU}"/></p:xfrm>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart">'
        '<c:chart r:id="rId3"/></a:graphicData></a:graphic></p:graphicFrame>'
    )
    slide2 = _p_slide(chart_frame + _p_sp(2, ["費用内訳"], _EMU // 2, _EMU // 4, ph="title") + table)
    chart = (
        f'{_XML}<c:chartSpace xmlns:c="{_NS_C}" xmlns:a="{_NS_A}"><c:chart>'
        "<c:title><c:tx><c:rich><a:bodyPr/><a:p><a:r><a:t>月別の推移</a:t></a:r></a:p></c:rich></c:tx></c:title>"
        "<c:plotArea><c:barChart><c:ser><c:idx val=\"0\"/>"
        "<c:tx><c:strRef><c:f>Sheet1!$B$1</c:f><c:strCache><c:ptCount val=\"1\"/><c:pt idx=\"0\"><c:v>売上高</c:v></c:pt></c:strCache></c:strRef></c:tx>"
        "<c:cat><c:strRef><c:f>Sheet1!$A$2:$A$3</c:f><c:strCache><c:ptCount val=\"2\"/>"
        "<c:pt idx=\"0\"><c:v>4月</c:v></c:pt><c:pt idx=\"1\"><c:v>5月</c:v></c:pt></c:strCache></c:strRef></c:cat>"
        "<c:val><c:numRef><c:f>Sheet1!$B$2:$B$3</c:f><c:numCache><c:ptCount val=\"2\"/>"
        "<c:pt idx=\"0\"><c:v>120</c:v></c:pt><c:pt idx=\"1\"><c:v>150</c:v></c:pt></c:numCache></c:numRef></c:val>"
        "</c:ser></c:barChart></c:plotArea></c:chart></c:chartSpace>"
    )
    notes = (f'{_XML}<p:notes xmlns:p="{_NS_P}" xmlns:a="{_NS_A}"><p:cSld><p:spTree>'
             + _p_sp(2, ["質疑では山田太郎が説明する。"], ph="body") + "</p:spTree></p:cSld></p:notes>")
    layout = (f'{_XML}<p:sldLayout xmlns:p="{_NS_P}" xmlns:a="{_NS_A}"><p:cSld><p:spTree>'
              + _p_sp(2, ["タイトル"], _EMU // 2, _EMU // 4, ph="title") + "</p:spTree></p:cSld></p:sldLayout>")
    members = {
        "[Content_Types].xml": _content_types([
            ("ppt/presentation.xml", "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"),
            ("ppt/slides/slide1.xml", "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"),
            ("ppt/slides/slide2.xml", "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"),
            ("ppt/charts/chart1.xml", "application/vnd.openxmlformats-officedocument.drawingml.chart+xml"),
        ]),
        "_rels/.rels": _rels([("rId1", "officeDocument", "ppt/presentation.xml")]),
        "ppt/presentation.xml": (f'{_XML}<p:presentation xmlns:p="{_NS_P}" xmlns:r="{_NS_R}"><p:sldIdLst>'
                                 '<p:sldId id="256" r:id="rId2"/><p:sldId id="257" r:id="rId3"/></p:sldIdLst></p:presentation>'),
        "ppt/_rels/presentation.xml.rels": _rels([("rId2", "slide", "slides/slide1.xml"), ("rId3", "slide", "slides/slide2.xml")]),
        "ppt/slides/slide1.xml": slide1,
        "ppt/slides/_rels/slide1.xml.rels": _rels([("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml"),
                                                   ("rId2", "notesSlide", "../notesSlides/notesSlide1.xml")]),
        "ppt/slides/slide2.xml": slide2,
        "ppt/slides/_rels/slide2.xml.rels": _rels([("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml"),
                                                   ("rId3", "chart", "../charts/chart1.xml")]),
        "ppt/slideLayouts/slideLayout1.xml": layout,
        "ppt/notesSlides/notesSlide1.xml": notes,
        "ppt/charts/chart1.xml": chart,
    }
    _write_zip(out / "deck.pptx", members)
    _truth(
        out, "deck.pptx", "pptx",
        text_blocks=["四半期報告", "売上は前年比10%増加した。", "費用は横ばいだった。", "第一四半期: 1,200万円",
                     "第二四半期: 1,500万円", "まとめ: 次回は7月に報告します。", "質疑では山田太郎が説明する。",
                     "費用内訳", "月別の推移", "売上高", "4月", "5月"],
        headings=["四半期報告", "費用内訳"],
        tables=[{"cells": PPTX_TABLE, "header_rows": 1}],
        typed_cells=[{"header": "金額", "value": "800万円", "kind": "number"}],
    )


# --- xlsx: merged title and data cell, era date formats, formula without cached value, furigana (rPh) ----------------------


def make_conversion_xlsx(out: Path) -> None:
    shared = [
        "<si><t>経費明細</t></si>",                     # 0
        "<si><t>申請日</t></si>", "<si><t>氏名</t></si>", "<si><t>区分</t></si>", "<si><t>金額</t></si>", "<si><t>税込</t></si>",  # 1-5
        '<si><t>山田太郎</t><rPh sb="0" eb="2"><t>ヤマダ</t></rPh><rPh sb="2" eb="4"><t>タロウ</t></rPh><phoneticPr fontId="1"/></si>',  # 6
        '<si><t>佐藤次郎</t><rPh sb="0" eb="2"><t>サトウ</t></rPh><rPh sb="2" eb="4"><t>ジロウ</t></rPh><phoneticPr fontId="1"/></si>',  # 7
        "<si><t>田中太郎</t></si>",                     # 8
        "<si><r><t>交通</t></r><r><rPr><b/></rPr><t>費</t></r></si>",  # 9 rich text
        "<si><t>宿泊費</t></si>",                       # 10
    ]
    sst = f'{_XML}<sst xmlns="{_NS_S}" count="{len(shared)}" uniqueCount="{len(shared)}">{"".join(shared)}</sst>'
    styles = (
        f'{_XML}<styleSheet xmlns="{_NS_S}"><numFmts count="2">'
        '<numFmt numFmtId="176" formatCode="[$-ja-JP]ggge&quot;年&quot;m&quot;月&quot;d&quot;日&quot;"/>'
        '<numFmt numFmtId="177" formatCode="[$-411]ge.m.d"/></numFmts>'
        '<cellXfs count="3"><xf numFmtId="0"/><xf numFmtId="176" applyNumberFormat="1"/><xf numFmtId="177" applyNumberFormat="1"/></cellXfs>'
        "</styleSheet>"
    )

    def s(ref, idx):
        return f'<c r="{ref}" t="s"><v>{idx}</v></c>'

    def n(ref, value, style=0):
        return f'<c r="{ref}"{f" s={chr(34)}{style}{chr(34)}" if style else ""}><v>{value}</v></c>'

    sheet_rows = [
        f'<row r="1">{s("A1", 0)}</row>',
        f'<row r="2">{s("A2", 1)}{s("B2", 2)}{s("C2", 3)}{s("D2", 4)}{s("E2", 5)}</row>',
        # 45383 = 2024-04-01 (令和6年), formula with a cached value
        f'<row r="3">{n("A3", 45383, 1)}{s("B3", 6)}{s("C3", 9)}{n("D3", 1200)}<c r="E3"><f>D3*1.1</f><v>1320</v></c></row>',
        # 43190 = 2018-03-31 (平成30年), C4 is covered by the merge C3:C4, E4's formula was never calculated
        f'<row r="4">{n("A4", 43190, 1)}{s("B4", 7)}{n("D4", 3500)}<c r="E4"><f>D4*1.1</f></c></row>',
        f'<row r="5">{n("A5", 45397, 2)}{s("B5", 8)}{s("C5", 10)}{n("D5", 12000)}<c r="E5"><f>D5*1.1</f><v>13200</v></c></row>',
    ]
    sheet = (f'{_XML}<worksheet xmlns="{_NS_S}"><sheetData>{"".join(sheet_rows)}</sheetData>'
             '<mergeCells count="2"><mergeCell ref="A1:E1"/><mergeCell ref="C3:C4"/></mergeCells></worksheet>')
    members = {
        "[Content_Types].xml": _content_types([
            ("xl/workbook.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"),
            ("xl/worksheets/sheet1.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"),
        ]),
        "_rels/.rels": _rels([("rId1", "officeDocument", "xl/workbook.xml")]),
        "xl/workbook.xml": (f'{_XML}<workbook xmlns="{_NS_S}" xmlns:r="{_NS_R}"><sheets>'
                            '<sheet name="経費" sheetId="1" r:id="rId1"/></sheets></workbook>'),
        "xl/_rels/workbook.xml.rels": _rels([("rId1", "worksheet", "worksheets/sheet1.xml"), ("rId2", "styles", "styles.xml"),
                                             ("rId3", "sharedStrings", "sharedStrings.xml")]),
        "xl/worksheets/sheet1.xml": sheet,
        "xl/sharedStrings.xml": sst,
        "xl/styles.xml": styles,
    }
    _write_zip(out / "book.xlsx", members)
    cells = [["経費明細", "", "", "", ""], ["申請日", "氏名", "区分", "金額", "税込"],
             ["令和6年4月1日", "山田太郎", "交通費", "1200", "1320"],
             ["平成30年3月31日", "佐藤次郎", "", "3500", "3850"],  # 3850: what Excel would show after recalculating
             ["R6.4.15", "田中太郎", "宿泊費", "12000", "13200"]]
    _truth(
        out, "book.xlsx", "xlsx",
        headings=["経費明細"],
        tables=[{"cells": cells, "merged": [{"row": 0, "col": 0, "rowspan": 1, "colspan": 5},
                                            {"row": 2, "col": 2, "rowspan": 2, "colspan": 1}],
                 "title_rows": 1, "header_rows": 1}],
        typed_cells=[
            {"header": "申請日", "value": "令和6年4月1日", "kind": "era_date"},
            {"header": "申請日", "value": "平成30年3月31日", "kind": "era_date"},
            {"header": "申請日", "value": "R6.4.15", "kind": "era_date"},
            {"header": "金額", "value": "12000", "kind": "number"},
            {"header": "税込", "value": "1320", "kind": "formula"},
        ],
        excluded_text=["ヤマダ", "タロウ", "サトウ", "ジロウ"],
        expected_warnings=["formula_without_cached_value"],
    )


# --- csv: the encodings Japanese offices produce, two of them without a BOM ---------------------------------------------------


def make_conversion_csvs(out: Path) -> None:
    import io

    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator="\r\n").writerows(CSV_ROWS)
    text = buffer.getvalue()
    for name, encoding, bom, expected in (
        ("csv_cp932.csv", "cp932", b"", "cp932"),
        ("csv_utf8_sig.csv", "utf-8", b"\xef\xbb\xbf", "utf-8-sig"),
        ("csv_utf16le_nobom.csv", "utf-16-le", b"", "utf-16-le"),
        ("csv_utf16be_nobom.csv", "utf-16-be", b"", "utf-16-be"),
    ):
        (out / name).write_bytes(bom + text.encode(encoding))
        _truth(
            out, name, "csv",
            tables=[{"cells": CSV_ROWS, "header_rows": 1}],
            typed_cells=[{"header": "金額", "value": "12,000", "kind": "number"},
                         {"header": "社員番号", "value": "1003", "kind": "number"}],
            expected_encoding=expected,
        )


def make_conversion_fixtures(out: Path) -> list:
    out.mkdir(parents=True, exist_ok=True)
    make_conversion_docx(out)
    make_conversion_pptx(out)
    make_conversion_xlsx(out)
    make_conversion_csvs(out)
    return sorted(p.name for p in out.iterdir())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data" / "external" / "dummy_docs"))
    ap.add_argument("--conversion-fixtures", nargs="?", const=str(ROOT / "tests" / "fixtures" / "conversion"), default=None,
                    help="write the conversion-accuracy fixtures (stdlib only) to this directory and stop")
    args = ap.parse_args()
    if args.conversion_fixtures:
        written = make_conversion_fixtures(Path(args.conversion_fixtures))
        print("created:", ", ".join(written))
        return 0
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    import japanize_matplotlib

    font = Path(japanize_matplotlib.__file__).parent / "fonts" / "ipaexg.ttf"
    make_pdfs(out, font)
    make_csvs(out)
    make_xlsx(out)
    truth = {"title": TITLE, "header": HEADER, "articles": ARTICLES, "steps": STEPS, "bullets": BULLETS, "table": TABLE,
             "qa": QA, "vertical": VERTICAL, "csv_rows": CSV_ROWS}
    (out / "truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8")
    print("created:", ", ".join(sorted(p.name for p in out.iterdir())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
