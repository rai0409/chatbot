"""Investigation: score the extraction / preprocessing pipeline on the dummy documents (tools/make_dummy_docs.py).

Purpose : measure, against known ground truth, how well PDFs, CSVs and Excel files come out of the repo's converters
          (rag_core.document_converters.convert_file_to_canonical_chunks): header/footer removal, wrapped-line
          joining, article/heading structure, circled steps, compatibility symbols, tables, quantities, Q&A
          pairs, vertical writing, CSV encodings and layouts, Excel formats. Run it on two code trees (for example
          HEAD and the working tree) to see what a change did.
Usage   : .venv/bin/python tools/eval_extraction_dummy.py [--root PATH] [--docs data/external/dummy_docs] [--json OUT]
            --root  the code tree to test (default: this repository); e.g. a `git archive HEAD` copy for "before"
Input   : data/external/dummy_docs/ (create it with tools/make_dummy_docs.py) and truth.json inside it
Output  : a table of checks on stdout (PASS/FAIL per check) and an optional JSON file inside the repository.
Cleanup : data/external/ is git-ignored; move it to 即削除/ when no longer needed.
Synthetic data only.
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def squash(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text or "").split())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(HERE))
    ap.add_argument("--docs", default=str(HERE / "data" / "external" / "dummy_docs"))
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    sys.path.insert(0, str(root))
    docs = Path(args.docs)
    truth = json.loads((docs / "truth.json").read_text(encoding="utf-8"))

    import config

    assert str(Path(config.__file__).resolve().parent) == str(root), f"config imported from {config.__file__}, not from --root"
    from rag_core.document_converters import convert_file_to_canonical_chunks as convert

    results: list[tuple[str, str, bool, str]] = []

    def check(group: str, name: str, ok: bool, detail: str = "") -> None:
        results.append((group, name, bool(ok), detail))

    def run(name: str, **kw):
        try:
            return convert(docs / name, tenant_id="default", source_doc=name, **kw), None
        except Exception as exc:  # noqa: BLE001
            return [], f"{type(exc).__name__}: {str(exc)[:80]}"

    # ---- regulation.pdf ---------------------------------------------------------------------------------------------------
    rows, err = run("regulation.pdf")
    g = "regulation.pdf"
    check(g, "converts", not err and bool(rows), err or f"{len(rows)} chunks")
    text_all = "\n".join(r["text"] for r in rows)
    parents = [r for r in rows if r.get("chunk_role") == "parent"]
    ptext = "\n".join(p["text"] for p in parents)
    check(g, "header removed from every chunk", "社内規程集" not in text_all and "- 1 -" not in text_all)
    check(g, "header never glued to the title/body", "規程集経費" not in squash(text_all) and "規程集第" not in squash(text_all))
    for heading, body in truth["articles"]:
        number = heading.split("（")[0]
        has_body = squash(body) in squash(ptext) or squash(body)[:40] in squash(ptext)
        in_path = any(number in " ".join(p.get("section_path") or []) or number in p["text"] for p in parents)
        check(g, f"{number}: heading kept (path or text)", in_path)
        check(g, f"{number}: body text intact (wrapped lines joined)", has_body)
    art4 = next((p for p in parents if "施行" in p["text"] or "令和6年" in squash(p["text"])), None)
    check(g, "steps/notes kept out of the last article", bool(art4) and "申請の手順" not in art4["text"] and "注意事項" not in art4["text"])
    check(g, "step marker ① kept", "①" in text_all and "②" in text_all)
    check(g, "step marker ① also searchable as 1", any("1" in (r.get("searchable_text") or "") for r in rows if "①" in r["text"]))
    for symbol in ("10㎏", "5℃", "第Ⅲ章"):
        check(g, f"stored text keeps {symbol}", symbol in text_all)
    for symbol in ("10kg", "5°c", "第iii章"):
        check(g, f"search text folds to {symbol}", symbol in "\n".join((r.get("searchable_text") or "") for r in rows).lower().replace("°C".lower(), "°c"))
    table_rows = [r for r in rows if r.get("chunk_type") == "table_row"]
    check(g, "table rows extracted (3)", len(table_rows) == 3, f"{len(table_rows)} rows")
    check(g, "table values exact", all(any(v in r["text"] for r in table_rows) for v in ("12,000円", "3,000円", "5,000円")))
    quantities = {q.get("canonical") for r in rows for q in (r.get("quantities") or [])}
    for wanted in ("10day", "5business_day", "FY2024", "22:00", "2024-04-01"):
        check(g, f"quantity {wanted}", wanted in quantities)

    # ---- faq.pdf ----------------------------------------------------------------------------------------------------------
    rows, err = run("faq.pdf")
    g = "faq.pdf"
    parents = [r for r in rows if r.get("chunk_role") == "parent" and r.get("faq_question")]
    for question, answer in truth["qa"]:
        q = squash(question).replace("?", "？")
        hit = next((r for r in parents if squash(r["faq_question"]).replace("?", "？") == q), None)
        check(g, f"question recovered: {question[:14]}…", hit is not None)
        check(g, f"answer clean: {question[:14]}…", bool(hit) and squash(answer) in squash(hit["text"]) and "A1." not in hit["text"] and "回答：" not in hit["text"])
    check(g, "no residue of Q1./質問２：/A. markers in questions", not any(m in r["faq_question"] for r in parents for m in ("Q1.", "質問2", "Q:")))

    # ---- vertical.pdf -----------------------------------------------------------------------------------------------------
    rows, err = run("vertical.pdf")
    g = "vertical.pdf"
    joined = squash("".join(r["text"] for r in rows if r.get("chunk_role") in (None, "parent")))
    expected = squash("".join(truth["vertical"]))
    check(g, "vertical writing read in order", expected in joined or joined.replace("\n", "") == expected, joined[:40])

    # ---- csv --------------------------------------------------------------------------------------------------------------
    for name in ("utf8.csv", "utf8_bom.csv", "cp932.csv", "utf16.csv"):
        rows, err = run(name)
        g = name
        check(g, "decoded and 3 data rows", not err and len(rows) == 3, err or f"{len(rows)} rows")
        blob = "\n".join(r["text"] for r in rows)
        check(g, "Japanese text intact", all(w in blob for w in ("山田太郎", "営業部", "出張費, 宿泊")))
        check(g, "multi-line cell kept", "会議の\n弁当代" in blob or "会議の 弁当代" in blob)
        check(g, "empty cell not lost", "未申請" in blob)
    rows, err = run("export_with_title.csv")
    g = "export_with_title.csv"
    blob = "\n".join(r["text"] for r in rows)
    check(g, "header row is the real header (社員番号…)", "社員番号:" in blob, f"{len(rows)} rows")
    check(g, "title/metadata lines are not data rows", not any(r["text"].startswith("経費一覧") or "出力日" in r["text"][:12] for r in rows))
    check(g, "trailing total row is flagged as a summary", any(r.get("row_kind") == "summary" for r in rows if "合計" in r["text"]))

    # ---- xlsx -------------------------------------------------------------------------------------------------------------
    rows, err = run("expenses.xlsx")
    g = "expenses.xlsx"
    blob = "\n".join(r["text"] for r in rows)
    check(g, "converts", not err and bool(rows), err or f"{len(rows)} rows")
    check(g, "dates as ISO", "2024-04-01" in blob and "2024-04-15" in blob)
    check(g, "currency amount kept", ("1,200円" in blob or "1200円" in blob))
    check(g, "percent", "10.0%" in blob or "10%" in blob)
    check(g, "hidden sheet not extracted", "非公開" not in blob)
    check(g, "merged title is not the header of the table", "経費明細:" not in blob)

    width = max(len(r[1]) for r in results) + 2
    current = None
    passed = 0
    for group, name, ok, detail in results:
        if group != current:
            print(f"\n[{group}]")
            current = group
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<{width}} {detail if not ok else ''}")
        passed += ok
    print(f"\n{passed}/{len(results)} checks passed   (code tree: {root})")
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps([{"group": a, "check": b, "pass": c, "detail": d} for a, b, c, d in results], ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
