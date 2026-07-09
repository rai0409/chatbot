"""Generate the synthetic multi-format sample documents (Prompt019/022).

Writes deterministic CSV/XLSX/DOCX/PPTX files (fixed zip timestamps, no
randomness) plus a small PDF when PyMuPDF is available. All content is
synthetic — no customer data. These samples back eval/cases/multiformat_*
and the onboarding dry-run tests.
"""

from __future__ import annotations

# --- bootstrap: add repo root to sys.path for script execution ---
import sys
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# --- end bootstrap ---

import argparse
import zipfile
from pathlib import Path

# Fixed timestamp so OOXML zip bytes are identical across runs.
_ZIP_DATE = (1980, 1, 1, 0, 0, 0)


def _zip_write(zf: zipfile.ZipFile, name: str, content: str) -> None:
    info = zipfile.ZipInfo(name, date_time=_ZIP_DATE)
    zf.writestr(info, content)


def write_csv(path: Path) -> None:
    rows = [
        "質問,回答,備考",
        "ポイントの有効期限はいつまでですか,ポイントは購入日から12ヶ月で失効します,会員規約第5条",
        "領収書は発行できますか,マイページから領収書をダウンロードできます,",
    ]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def write_xlsx(path: Path) -> None:
    workbook = (
        '<?xml version="1.0"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="FAQ" sheetId="1" r:id="rId1"/>'
        '<sheet name="料金" sheetId="2" r:id="rId2"/></sheets></workbook>'
    )
    rels = (
        '<?xml version="1.0"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>'
        "</Relationships>"
    )

    def _inline_row(row_number: int, values: list[str]) -> str:
        cells = "".join(
            f'<c r="{chr(ord("A") + i)}{row_number}" t="inlineStr"><is><t>{v}</t></is></c>'
            for i, v in enumerate(values)
        )
        return f'<row r="{row_number}">{cells}</row>'

    def _sheet(rows: list[list[str]]) -> str:
        body = "".join(_inline_row(i + 1, row) for i, row in enumerate(rows))
        return (
            '<?xml version="1.0"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f"<sheetData>{body}</sheetData></worksheet>"
        )

    faq = _sheet(
        [
            ["質問", "回答"],
            ["解約金はかかりますか", "最低利用期間内の解約には解約金5000円がかかります"],
            ["プラン変更はいつ反映されますか", "プラン変更は翌請求サイクルから反映されます"],
        ]
    )
    pricing = _sheet(
        [
            ["プラン", "月額"],
            ["スタンダードプラン", "9800円"],
            ["エンタープライズプラン", "49800円"],
        ]
    )
    ct = '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
    with zipfile.ZipFile(path, "w") as zf:
        _zip_write(zf, "[Content_Types].xml", ct)
        _zip_write(zf, "xl/workbook.xml", workbook)
        _zip_write(zf, "xl/_rels/workbook.xml.rels", rels)
        _zip_write(zf, "xl/sharedStrings.xml", '<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>')
        _zip_write(zf, "xl/worksheets/sheet1.xml", faq)
        _zip_write(zf, "xl/worksheets/sheet2.xml", pricing)


def write_docx(path: Path) -> None:
    def p(text: str, style: str | None = None) -> str:
        style_xml = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        return f"<w:p>{style_xml}<w:r><w:t>{text}</w:t></w:r></w:p>"

    def tbl(rows: list[list[str]]) -> str:
        out = "<w:tbl>"
        for cells in rows:
            out += "<w:tr>" + "".join(f"<w:tc>{p(c)}</w:tc>" for c in cells) + "</w:tr>"
        return out + "</w:tbl>"

    body = (
        p("バックアップ運用", style="Heading1")
        + p("データベースのバックアップは毎日午前2時に自動取得されます。")
        + p("リストア手順は運用管理者が実施します。")
        + p("お問い合わせFAQ", style="Heading1")
        + tbl(
            [
                ["質問", "回答"],
                ["パスワードを忘れた場合の手続きは", "ログイン画面の再設定リンクから本人確認のうえ再設定します"],
            ]
        )
    )
    document = (
        '<?xml version="1.0"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        _zip_write(zf, "word/document.xml", document)


def write_pptx(path: Path) -> None:
    def slide(title: str | None, body_lines: list[str], table_rows: list[list[str]] | None = None) -> str:
        shapes = ""
        if title:
            shapes += (
                '<p:sp><p:nvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>'
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
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            f"<p:cSld><p:spTree>{shapes}{table}</p:spTree></p:cSld></p:sld>"
        )

    slides = [
        slide("サービス導入手順", ["キックオフ後、初期設定は5営業日で完了します。"]),
        slide(None, [], table_rows=[["項目", "目標値"], ["稼働率", "99.9パーセント"]]),
    ]
    with zipfile.ZipFile(path, "w") as zf:
        for i, xml in enumerate(slides, start=1):
            _zip_write(zf, f"ppt/slides/slide{i}.xml", xml)


def write_pdf(path: Path) -> bool:
    try:
        import fitz  # type: ignore
    except Exception:
        return False
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (72, 72),
        "Sample security policy (synthetic). Encryption key rotation happens"
        " every 90 days. Incident reports go to SOC-DESK-01 within 24 hours.",
    )
    doc.save(str(path))
    doc.close()
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate deterministic synthetic sample documents.")
    parser.add_argument("--output-dir", default="eval/cases/sample_docs")
    args = parser.parse_args(argv)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    write_csv(out / "sample_faq.csv")
    write_xlsx(out / "sample_plans.xlsx")
    write_docx(out / "sample_manual.docx")
    write_pptx(out / "sample_deck.pptx")
    pdf_ok = write_pdf(out / "sample_policy.pdf")

    written = ["sample_faq.csv", "sample_plans.xlsx", "sample_manual.docx", "sample_deck.pptx"]
    if pdf_ok:
        written.append("sample_policy.pdf")
    print(f"Summary: output_dir={out} written={','.join(written)} pdf={'ok' if pdf_ok else 'skipped (PyMuPDF unavailable)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
