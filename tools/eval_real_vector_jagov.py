"""Investigation: retrieval quality with REAL models on JaGovFaqs-22k (BM25 vs dense vs hybrid vs hybrid+rerank).

Purpose : a measured baseline of the product's retrieval components on Japanese FAQ data: Sudachi-BM25,
          ruri-v3-130m dense retrieval (the repo's LocalEmbeddingProvider, with its prefixes), weighted RRF (k=60)
          and the cross-encoder rerank of the top 30 (ruri-v3-reranker-310m, the repo default).
Usage   : HF_HOME=$PWD/data/external/hf_cache .venv/bin/python tools/eval_real_vector_jagov.py \
              [--docs 3000] [--queries 150] [--rerank-queries 100] [--seed 7]
Input   : data/external/jagovfaqs/data.jsonl (JaGovFaqs-22k, CC-BY-4.0; Question / Answer; not committed)
Queries : made from the dataset itself, so they are formal and easy: "keyword" (content words of the question
          only) and "partial" (the first 55% of the question). Real user phrasing (colloquial, typos) is NOT
          covered: treat the numbers as a component baseline, not as production accuracy.
Output  : stdout table + data/external/eval_out/real_vector_jagov.json (inside the repo, git-ignored).
Cleanup : data/external/ and the model cache are git-ignored; move them to 即削除/ when no longer needed.
Public government text only: no personal data.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA = ROOT / "data" / "external"


def rrf(rankings, k=60, weights=None):
    scores = {}
    for ranking, weight in zip(rankings, weights or [1.0] * len(rankings)):
        for rank, doc in enumerate(ranking, start=1):
            scores[doc] = scores.get(doc, 0.0) + weight / (k + rank)
    return [d for d, _ in sorted(scores.items(), key=lambda kv: -kv[1])]


def metrics(ranked_lists, golds):
    n = len(golds)
    out = {}
    for k in (1, 5, 10):
        out[f"R@{k}"] = round(100 * sum(g in r[:k] for r, g in zip(ranked_lists, golds)) / n, 1)
    out["MRR@10"] = round(100 * sum((1 / (r[:10].index(g) + 1)) if g in r[:10] else 0 for r, g in zip(ranked_lists, golds)) / n, 1)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", type=int, default=3000)
    ap.add_argument("--queries", type=int, default=150)
    ap.add_argument("--rerank-queries", type=int, default=100)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    import numpy as np
    from rank_bm25 import BM25Okapi

    from rag_core import ja_tokenizer
    from rag_core.embedding_provider import LocalEmbeddingProvider

    rows = [json.loads(l) for l in (DATA / "jagovfaqs" / "data.jsonl").read_text(encoding="utf-8").splitlines()]
    rows = [r for r in rows if 10 <= len(r["Question"]) <= 120 and 20 <= len(r["Answer"]) <= 600]
    random.Random(args.seed).shuffle(rows)
    rows = rows[: args.docs]
    docs = [f"Q: {r['Question'].strip()}\nA: {r['Answer'].strip()[:300]}" for r in rows]
    t0 = time.time()

    def toks(text):
        return ja_tokenizer.sudachi_tokens(unicodedata.normalize("NFKC", text))

    bm25 = BM25Okapi([toks(d) or ["_"] for d in docs])
    provider = LocalEmbeddingProvider()
    doc_vecs = np.array(provider.embed_documents(docs), dtype="float32")
    print(f"indexed {len(docs)} docs in {time.time() - t0:.0f}s (model {provider.model_name})", flush=True)

    sample = list(range(len(rows)))
    random.Random(args.seed + 1).shuffle(sample)
    sample = sample[: args.queries]

    def keyword_query(q):
        words = ja_tokenizer.morpheme_tokens(unicodedata.normalize("NFKC", q))
        return " ".join(words[:6]) or q

    def partial_query(q):
        return q[: max(6, int(len(q) * 0.55))]

    from rag_core import cross_encoder_reranker as ce
    import config
    config.CROSS_ENCODER_TOP_N = 30

    result = {"docs": len(docs), "queries": len(sample), "model": provider.model_name, "variants": {}}
    for name, make in (("keyword", keyword_query), ("partial", partial_query)):
        queries = [make(rows[i]["Question"]) for i in sample]
        q_vecs = np.array(provider.embed_queries(queries), dtype="float32")
        dense_rank, bm25_rank, hybrid_rank = [], [], []
        for qi, q in enumerate(queries):
            d = list(np.argsort(-(doc_vecs @ q_vecs[qi]))[:50])
            b = list(np.argsort(-bm25.get_scores(toks(q) or ["_"]))[:50])
            dense_rank.append(d); bm25_rank.append(b); hybrid_rank.append(rrf([b, d]))
        golds = sample
        variant = {"bm25": metrics(bm25_rank, golds), "dense": metrics(dense_rank, golds), "hybrid_rrf": metrics(hybrid_rank, golds)}
        # rerank the top 30 of the hybrid ranking (a subset of the queries: CPU cost)
        m = min(args.rerank_queries, len(sample))
        reranked = []
        from rag_core.retrieval import RetrievedChunk
        for qi in range(m):
            head = hybrid_rank[qi][:30]
            chunks = [RetrievedChunk(text=docs[i], metadata={"idx": int(i)}, score=0.0) for i in head]
            out = ce.rerank(queries[qi], chunks)
            reranked.append([c.metadata["idx"] for c in out])
        variant["hybrid_rrf (same queries)"] = metrics(hybrid_rank[:m], golds[:m])
        variant["hybrid+rerank"] = metrics(reranked, golds[:m])
        variant["_rerank_queries"] = m
        result["variants"][name] = variant
        print(f"\n[{name}] ({time.time() - t0:.0f}s)", flush=True)
        for system, mm in variant.items():
            if system.startswith("_"):
                continue
            print(f"  {system:<28}" + "  ".join(f"{k} {v:>5}" for k, v in mm.items()), flush=True)
    out = DATA / "eval_out"
    out.mkdir(exist_ok=True)
    (out / "real_vector_jagov.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
