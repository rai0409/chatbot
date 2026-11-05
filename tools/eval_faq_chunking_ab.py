"""Investigation: does the FAQ chunking change help or hurt retrieval? (A/B: main vs working tree)

Purpose : measure (1) how faithfully FAQ entries are recovered when a PDF-style FAQ (lines wrapped at ~30
          characters, three common layouts) is chunked, and (2) BM25 retrieval of the right entry from
          the chunks' searchable_text, for the OLD chunker (git HEAD copy) and the NEW one (working tree).
Usage   : .venv/bin/python tools/eval_faq_chunking_ab.py [--n 1500] [--seed 7]
Input   : data/external/jagovfaqs/data.jsonl  (JaGovFaqs-22k, CC-BY-4.0; Question / Answer; not committed)
          data/external/chunking_ja_main.py   (`git show HEAD:rag_core/chunking_ja.py`, not committed)
Output  : a table on stdout and data/external/eval_out/faq_chunking_ab.json (inside the repo, git-ignored).
Cleanup : data/external/ holds third-party data and is git-ignored; move it to 即削除/ when no longer needed.
No personal data: public government FAQ text only.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import random
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA = ROOT / "data" / "external"


def _load_old():
    spec = importlib.util.spec_from_file_location("chunking_ja_main", DATA / "chunking_ja_main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def wrap(text: str, width: int = 30) -> str:
    out = []
    for para in str(text).split("\n"):
        para = para.strip()
        while len(para) > width:
            out.append(para[:width])
            para = para[width:]
        if para:
            out.append(para)
    return "\n".join(out)


LAYOUTS = {
    "colon":  lambda i, q, a: f"Q: {wrap(q)}\nA: {wrap(a)}",
    "number": lambda i, q, a: f"Q{i}. {wrap(q)}\nA{i}. {wrap(a)}",
    "jp":     lambda i, q, a: f"質問{i}：{wrap(q)}\n回答：{wrap(a)}",
}


def squash(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text or "").split())


def entries_from(module, text: str):
    rows = module.build_ja_chunk_records(doc_id="d", source_doc="f.pdf", text=text, doc_type="faq", title="", source_pages=[1])
    return [r for r in rows if r.get("chunk_role") == "parent"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1500)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--per-page", type=int, default=6)
    args = ap.parse_args()

    from rag_core import chunking_ja as new, ja_tokenizer
    from rank_bm25 import BM25Okapi

    old = _load_old()
    rows = [json.loads(line) for line in (DATA / "jagovfaqs" / "data.jsonl").read_text(encoding="utf-8").splitlines()]
    rows = [r for r in rows if 5 <= len(r["Question"]) <= 120 and 10 <= len(r["Answer"]) <= 400]
    random.Random(args.seed).shuffle(rows)
    rows = rows[: args.n]
    result = {"n": len(rows), "tokenizer": ja_tokenizer.active_tokenizer_name(), "layouts": {}}

    for layout, render in LAYOUTS.items():
        stats = {}
        for name, module in (("old", old), ("new", new)):
            q_ok = q_ok_nospace = a_ok = found = 0
            docs, meta = [], []
            for start in range(0, len(rows), args.per_page):
                page = rows[start:start + args.per_page]
                text = "\n\n".join(render(start + j + 1, r["Question"], r["Answer"]) for j, r in enumerate(page))
                parents = entries_from(module, text)
                for j, r in enumerate(page):
                    gold_q, gold_a = unicodedata.normalize("NFKC", r["Question"]).strip(), squash(r["Answer"])
                    hit = next((p for p in parents if squash(p.get("faq_question", "")) == squash(r["Question"])), None)
                    if hit is not None:
                        found += 1
                        q_ok_nospace += 1
                        q_ok += int(hit["faq_question"].strip().replace("？", "?") == gold_q.replace("？", "?"))
                        a_ok += int(gold_a in squash(hit["text"]) and "A:" in hit["text"] and "Q: Q" not in hit["text"])
                for p in parents:
                    docs.append(p["searchable_text"])
                    meta.append(squash(p.get("faq_question", "")))
            total = len(rows)
            # BM25 over the searchable_text of all parent chunks; query = the original question
            tokens = [ja_tokenizer.sudachi_tokens(unicodedata.normalize("NFKC", d)) for d in docs]
            bm25 = BM25Okapi([t or ["_"] for t in tokens])
            top1 = top5 = 0
            sample = rows[:400]
            for r in sample:
                q = ja_tokenizer.sudachi_tokens(unicodedata.normalize("NFKC", r["Question"]))
                scores = bm25.get_scores(q)
                order = sorted(range(len(scores)), key=lambda i: -scores[i])[:5]
                gold = squash(r["Question"])
                top1 += int(bool(order) and meta[order[0]] == gold)
                top5 += int(any(meta[i] == gold for i in order))
            stats[name] = {
                "entries_recovered_%": round(100 * found / total, 1),
                "question_exact_%": round(100 * q_ok / total, 1),
                "answer_clean_%": round(100 * a_ok / total, 1),
                "bm25_recall@1_%": round(100 * top1 / len(sample), 1),
                "bm25_recall@5_%": round(100 * top5 / len(sample), 1),
                "chunks": len(docs),
            }
        result["layouts"][layout] = stats
    out = DATA / "eval_out"
    out.mkdir(exist_ok=True)
    (out / "faq_chunking_ab.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"n={result['n']} tokenizer={result['tokenizer']}")
    for layout, stats in result["layouts"].items():
        print(f"\n[{layout}]")
        keys = list(stats["old"])
        print(f"{'metric':<22}{'old':>10}{'new':>10}")
        for k in keys:
            print(f"{k:<22}{stats['old'][k]:>10}{stats['new'][k]:>10}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
