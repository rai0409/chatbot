"""Investigation: build DUMMY Japanese business documents with known ground truth.

Purpose : test the extraction / preprocessing pipeline on realistic inputs without real documents.
          Creates text PDFs (a regulation with headers/footers, wrapped lines, circled steps, a ruled table and
          compatibility characters; a Q&A list in three layouts; a vertical-writing page), CSV files in the
          encodings Japanese offices use, and an Excel file with merged cells, dates and a hidden sheet.
Usage   : .venv/bin/python tools/make_dummy_docs.py [--out data/external/dummy_docs]
Needs   : reportlab, openpyxl, and a Japanese font (the IPAex Gothic bundled in the `japanize-matplotlib` package:
          pip install reportlab openpyxl japanize-matplotlib). No network, no real data.
Output  : the documents and truth.json (what each document says) inside the repository (data/external/, git-ignored).
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data" / "external" / "dummy_docs"))
    args = ap.parse_args()
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
