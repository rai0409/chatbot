"""Investigation: precision / recall of the font-based heading detection (docextract PdfPage.heading_lines).

Purpose : measure how often a line is wrongly taken for a heading (false positives) and how many real headings are
          missed, on generated Japanese PDFs with known headings, for different size ratios and layouts. Use it to
          choose DOCEXTRACT heading settings before trusting them on real documents.
Usage   : .venv/bin/python tools/eval_heading_detection.py [--seed 7] [--docs 60] [--out data/external/heading_eval]
Needs   : reportlab and the IPAex Gothic font of `japanize-matplotlib` (pip install reportlab japanize-matplotlib).
Output  : a table on stdout and heading_eval.json in --out (inside the repository, git-ignored).
Cleanup : move data/external/heading_eval to 即削除/ when no longer needed.
Synthetic text only (invented regulation sentences). Bold headings are Latin words set in Helvetica-Bold (the Japanese
test font has no bold face): they test the detection rule, not how real Japanese bold fonts are named in your documents.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BODY = [
    "この規程は、従業員が業務のために支出した費用の精算に関する手続きを定めることを目的とする。",
    "経費の精算は、支出した日から十日以内に所定の様式により申請しなければならない。",
    "申請された経費は、所属長が内容を確認したうえで経理部門に提出するものとする。",
    "領収書のない支出は、原則として精算の対象とせず、やむを得ない場合に限り支払証明書を認める。",
    "交通費は最も経済的な経路による実費とし、定期券の区間に重複する部分は支給しない。",
    "出張に伴う宿泊費の上限は別表に定め、上限を超える場合は事前に承認を得なければならない。",
]
LATIN_HEADINGS = ["Scope", "Procedure", "Definitions", "Notes", "Deadline", "Exceptions", "Contact"]
HEADINGS = ["総則", "申請の手順", "対象となる経費", "注意事項", "精算の期限", "例外の扱い", "別表の見方", "問い合わせ先"]
CAPTIONS = ["図1 申請の流れ", "表2 宿泊費の上限", "注1 金額は税込", "出典 社内資料", "備考 本表は参考値"]
EMPHASIS = ["原則として", "事前の承認", "必ず", "領収書"]


def wrap(text, width):
    return [text[i:i + width] for i in range(0, len(text), width)]


def make_doc(path: Path, rng: random.Random, style: dict, fonts: tuple[str, str]) -> list[list[str]]:
    """Writes one PDF; returns the truth: per page, the heading lines (as extracted text)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    regular, bold = fonts
    width, height = A4
    c = canvas.Canvas(str(path), pagesize=A4)
    truth: list[list[str]] = []
    body_size = style["body"]
    for _page in range(style["pages"]):
        y = height - 80
        page_truth: list[str] = []
        c.setFont(regular, 9)
        c.drawString(60, height - 36, "株式会社サンプル 社内規程集")
        for _section in range(rng.randint(2, 4)):
            heading = rng.choice(HEADINGS)
            if style["heading"] == "size":
                c.setFont(regular, body_size * style["ratio"])
            elif style["heading"] == "bold":
                heading = rng.choice(LATIN_HEADINGS)
                c.setFont("Helvetica-Bold", body_size)
            else:
                c.setFont(regular, body_size)  # headings look like body text: undetectable by design
            c.drawString(60, y, heading)
            if style["heading"] != "none":
                page_truth.append(heading)
            y -= body_size * style["ratio"] + 8
            c.setFont(regular, body_size)
            for _para in range(rng.randint(1, 2)):
                jitter = 1 + rng.uniform(-style.get("noise", 0), style.get("noise", 0))
                c.setFont(regular, body_size * jitter)
                for line in wrap(rng.choice(BODY), 32):
                    c.drawString(60, y, line)
                    y -= body_size + 5
                c.setFont(regular, body_size)
                y -= 6
                if style.get("items"):  # short list items without a final period
                    for item in rng.sample(["提出期限を守る", "領収書を添付する", "上長の承認を得る", "私費を含めない"], 2):
                        c.drawString(72, y, "・" + item)
                        y -= body_size + 4
            if style.get("emphasis"):  # a bold word inside a body line: not a heading
                c.setFont(regular, body_size)
                text = "この点は" + rng.choice(EMPHASIS) + "のとおり扱うこと"
                c.drawString(60, y, text)
                c.setFont("Helvetica-Bold", body_size)
                c.drawString(60 + body_size * len(text), y, " NOTE")
                c.setFont(regular, body_size)
                y -= body_size + 8
            if style.get("caption"):  # larger caption / pull-quote lines: not headings
                c.setFont(regular, body_size * style["caption"])
                c.drawString(60, y, rng.choice(CAPTIONS))
                c.setFont(regular, body_size)
                y -= body_size * style["caption"] + 8
        truth.append(page_truth)
        c.showPage()
    c.save()
    return truth


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--docs", type=int, default=60)
    ap.add_argument("--out", default=str(ROOT / "data" / "external" / "heading_eval"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    import japanize_matplotlib
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    font = Path(japanize_matplotlib.__file__).parent / "fonts" / "ipaexg.ttf"
    pdfmetrics.registerFont(TTFont("IPAex", str(font)))
    pdfmetrics.registerFont(TTFont("IPAex-Bold", str(font)))  # same glyphs, a name that says "bold"

    from rag_core import extraction

    extraction.load()
    from docextract.pdf import open_pdf

    rng = random.Random(args.seed)
    # (label, style): the layouts a real document set is likely to contain
    scenarios = []
    for ratio in (1.05, 1.1, 1.15, 1.25, 1.5):
        scenarios.append((f"size x{ratio}", {"heading": "size", "ratio": ratio}))
    scenarios += [
        ("bold (Helvetica-Bold), same size", {"heading": "bold", "ratio": 1.0}),
        ("size x1.25 + bold word inside lines", {"heading": "size", "ratio": 1.25, "emphasis": True}),
        ("size x1.15 + size noise +-4% + list items", {"heading": "size", "ratio": 1.15, "noise": 0.04, "items": True}),
        ("no headings + size noise +-6% + items", {"heading": "none", "ratio": 1.0, "noise": 0.06, "items": True}),
        ("size x1.25 + larger captions (x1.2)", {"heading": "size", "ratio": 1.25, "caption": 1.2}),
        ("size x1.25 + larger captions (x1.4)", {"heading": "size", "ratio": 1.25, "caption": 1.4}),
        ("no visual headings (plain text)", {"heading": "none", "ratio": 1.0}),
        ("no headings, larger captions (x1.3)", {"heading": "none", "ratio": 1.0, "caption": 1.3}),
    ]
    ratios = [1.05, 1.08, 1.12, 1.15, 1.2, 1.3]
    per = max(2, args.docs // len(scenarios))
    report = {}
    for label, base in scenarios:
        counts = {r: [0, 0, 0] for r in ratios}  # tp, fp, fn
        for n in range(per):
            style = dict(base, pages=3, body=rng.choice([10, 10.5, 11, 12]))
            path = out / f"{label.replace(' ', '_').replace('+', 'p').replace('(', '').replace(')', '').replace('/', '')}_{n}.pdf"
            truth = make_doc(path, rng, style, ("IPAex", "IPAex-Bold"))
            with open_pdf(path) as doc:
                for page, expected in zip(doc.pages, truth):
                    for ratio in ratios:
                        found = page.heading_lines(ratio=ratio)
                        want = [" ".join(h.split()) for h in expected]
                        got = [" ".join(h.split()) for h in found]
                        tp = sum(1 for g in got if g in want)
                        counts[ratio][0] += tp
                        counts[ratio][1] += len(got) - tp
                        counts[ratio][2] += max(0, len(want) - tp)
        report[label] = {str(r): {"tp": v[0], "fp": v[1], "fn": v[2]} for r, v in counts.items()}

    print(f"{'scenario':<40}" + "".join(f"  r={r:<4} P/R      " for r in ratios))
    totals = {r: [0, 0, 0] for r in ratios}
    for label, per_ratio in report.items():
        line = f"{label:<40}"
        for r in ratios:
            v = per_ratio[str(r)]
            tp, fp, fn = v["tp"], v["fp"], v["fn"]
            for i, x in enumerate((tp, fp, fn)):
                totals[r][i] += x
            prec = f"{100 * tp / (tp + fp):3.0f}" if tp + fp else "  -"
            rec = f"{100 * tp / (tp + fn):3.0f}" if tp + fn else "  -"
            line += f"  {prec}/{rec} (fp{fp:<3})  "
        print(line)
    print("-" * 40)
    line = f"{'ALL':<40}"
    for r in ratios:
        tp, fp, fn = totals[r]
        line += f"  {100 * tp / max(1, tp + fp):3.0f}/{100 * tp / max(1, tp + fn):3.0f} (fp{fp:<3})  "
    print(line)
    (out / "heading_eval.json").write_text(json.dumps({"ratios": ratios, "scenarios": report}, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
