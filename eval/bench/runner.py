"""Run a grid of retrieval configurations against an evaluation set.

Expensive stages are computed once and reused by the nested loops:
chunking -> lexical index / embeddings -> dimension -> quantization ->
backend (dense search) -> fusion (in memory) -> rerank depth (cached scores).
Fusion calls rag_core.retrieval._fuse_hybrid_buckets, so the numbers measure
the production fusion code. Every evaluated configuration appends one line
to summary.jsonl and writes per_query/<config_id>.jsonl for paired tests.

Fusion methods: weighted_rrf | dbsf (production) and dense_only |
lexical_only (single-lane baselines, e.g. dense Recall@100 per model).
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

import config
from rag_core.retrieval import RetrievedChunk, _fuse_hybrid_buckets
from eval.bench import corpus as corpus_mod
from eval.bench import conditions
from eval.bench import holdout
from eval.bench.backends import make_backend
from eval.bench.dataset import EvalQuery, check_match_mode, file_fingerprint, load_eval_set
from eval.bench.encoders import DenseIndex, EmbeddingSpec, Projection, encode, single_query_latency
from eval.bench.lexical import LexicalHit, LexicalIndex, LexicalSpec
from eval.bench.metrics import RankedChunk, aggregate, credit_by_rank, query_metrics
from eval.bench.rerank_cache import CrossEncoderScorer
from eval.bench.stats import bootstrap_ci, latency_summary

RUN_SCHEMA = "bench-run-v1"
DEFAULT_KS = (1, 3, 5, 10, 20, 50, 100)
DEFAULT_CI_METRICS = ("recall@10", "ndcg@10", "mrr@10", "hit@10")
FUSION_METHODS = ("weighted_rrf", "dbsf", "dense_only", "lexical_only")


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]


def short_hash(obj: Any) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]


def _git_commit() -> Optional[str]:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=config.BASE_DIR, text=True, capture_output=True, check=True
        ).stdout.strip()
    except Exception:
        return None


def grid(cfg: Dict[str, Any]) -> Dict[str, List[Any]]:
    return {
        "chunking": _as_list(cfg.get("chunking", {"mode": "as_is"})),
        "lexical": _as_list(cfg.get("lexical", {})),
        "embedding": _as_list(cfg["embedding"]),
        "dimension": _as_list(cfg.get("dimension", "none")),
        "quantization": _as_list(cfg.get("quantization", {"type": "float32"})),
        "backend": _as_list(cfg.get("dense_backend", {"type": "exact"})),
        "fusion": _as_list(cfg.get("fusion", {"method": "weighted_rrf", "k": 60})),
        "rerank": _as_list(cfg.get("rerank", [])),
    }


def _valid_dense(backend: Dict[str, Any], quant: Dict[str, Any]) -> bool:
    # Chroma has no vector quantization; such combinations are skipped.
    return not (backend.get("type", "exact") == "chroma" and quant.get("type", "float32") != "float32")


def plan(cfg: Dict[str, Any]) -> Dict[str, Any]:
    cfg, _generation = conditions.apply_generation(cfg)
    g = grid(cfg)
    dense = sum(
        1
        for _c in g["chunking"]
        for _e in g["embedding"]
        for _d in g["dimension"]
        for q in g["quantization"]
        for b in g["backend"]
        if _valid_dense(b, q)
    )
    rerank_variants = 1 + sum(len(_as_list(r.get("depths", [30]))) for r in g["rerank"] if r.get("model"))
    return {
        "factors": {name: len(values) for name, values in g.items()},
        "dense_indexes": dense,
        "lexical_indexes": len(g["chunking"]) * len(g["lexical"]),
        "evaluations": dense * len(g["lexical"]) * len(g["fusion"]) * rerank_variants,
    }


def _fusion_settings(spec: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    method = str(spec.get("method", "weighted_rrf"))
    if method not in FUSION_METHODS:
        raise ValueError(f"unknown fusion method '{method}' (supported: {', '.join(FUSION_METHODS)})")
    settings = {
        "method": method,
        "dense_weight": float(spec.get("dense_weight", 1.0)),
        "keyword_weight": float(spec.get("keyword_weight", 1.0)),
        "exact_weight": float(spec.get("exact_weight", 1.0)),
    }
    return settings, int(spec.get("k", 60))


def fuse_one(
    dense_hits: Sequence[Tuple[int, float]],
    lexical_hits: Sequence[LexicalHit],
    settings: Dict[str, Any],
    k_rrf: int,
    ids: Optional[Sequence[str]] = None,
) -> Tuple[List[int], List[float]]:
    """Fused ranking (chunk indexes) and scores, highest first.

    ids: chunk ids by index. Ties are broken by lexical presence, then by chunk id as in production
    (rag_core.retrieval._rank_buckets); without ids, by chunk index."""
    if settings["method"] == "dense_only":
        return [i for i, _ in dense_hits], [s for _, s in dense_hits]
    if settings["method"] == "lexical_only":
        return [h.index for h in lexical_hits], [h.bm25 for h in lexical_hits]
    merged: Dict[int, Dict[str, Any]] = {}

    def _bucket(idx: int) -> Dict[str, Any]:
        return merged.setdefault(
            idx,
            {"vector": None, "keyword": None, "dense_rank": None, "keyword_rank": None, "exact_rank": None, "fused": 0.0},
        )

    for rank, (idx, score) in enumerate(dense_hits, start=1):
        bucket = _bucket(idx)
        bucket["vector"] = RetrievedChunk(text="", metadata={"vector_distance": 1.0 - score}, score=1.0 - score)
        bucket["dense_rank"] = rank
    for rank, hit in enumerate(lexical_hits, start=1):
        bucket = _bucket(hit.index)
        bucket["keyword"] = RetrievedChunk(text="", metadata={"bm25_score": hit.bm25}, score=0.0)
        bucket["keyword_rank"] = rank
    exact = [hit for hit in lexical_hits if hit.quoted_hits > 0 or hit.id_hits > 0]
    for rank, hit in enumerate(exact, start=1):
        _bucket(hit.index)["exact_rank"] = rank
    _fuse_hybrid_buckets(merged, k_rrf=k_rrf, settings=settings)
    order = sorted(
        sorted(merged.items(), key=(lambda kv: ids[kv[0]]) if ids is not None else (lambda kv: kv[0])),
        key=lambda kv: (kv[1]["fused"], kv[1]["keyword"] is not None),
        reverse=True,  # sorted() keeps the order of equal keys: ties stay in chunk-id (or index) order
    )
    return [idx for idx, _ in order], [float(bucket["fused"]) for _, bucket in order]


def _run_lexical(chunks, spec_cfg: Dict[str, Any], queries: Sequence[EvalQuery], top: int) -> Dict[str, Any]:
    index = LexicalIndex(chunks, LexicalSpec(**spec_cfg))
    results, ms = {}, {}
    for q in queries:
        started = time.perf_counter()
        results[q.query_id] = index.search(q.query, top, q.tenant_id)
        ms[q.query_id] = (time.perf_counter() - started) * 1000.0
    return {"spec": spec_cfg, "results": results, "ms": ms, "describe": index.describe()}


def _run_dense(backend_cfg, quant_cfg, corpus: np.ndarray, query_matrix: np.ndarray, tenants, queries, top: int) -> Dict[str, Any]:
    backend = make_backend(backend_cfg, quant_cfg)
    backend.build(corpus, tenants)
    results, ms = {}, {}
    try:
        for row, q in enumerate(queries):
            started = time.perf_counter()
            results[q.query_id] = backend.search(query_matrix[row], top, q.tenant_id)
            ms[q.query_id] = (time.perf_counter() - started) * 1000.0
        describe = backend.describe()
    finally:
        backend.close()
    return {"results": results, "ms": ms, "describe": describe}


def _exact_reference(corpus: np.ndarray, query_matrix: np.ndarray, tenants, queries, top: int) -> Dict[str, List[int]]:
    index = DenseIndex(corpus, tenants)
    return {q.query_id: [i for i, _ in index.search(query_matrix[row], top, q.tenant_id)] for row, q in enumerate(queries)}


def _ann_recall(results: Dict[str, List[Tuple[int, float]]], reference: Dict[str, List[int]], k: int) -> float:
    shares = []
    for qid, ref in reference.items():
        expected = set(ref[:k])
        if expected:
            got = {i for i, _ in results.get(qid, [])[:k]}
            shares.append(len(got & expected) / len(expected))
    return sum(shares) / len(shares) if shares else float("nan")


class _Writer:
    def __init__(self, run_dir: Path, queries: Sequence[EvalQuery], views: List[RankedChunk], settings: Dict[str, Any]):
        self.run_dir = run_dir
        self.queries = queries
        self.views = views
        self.s = settings
        self.count = 0

    def evaluate(
        self,
        record: Dict[str, Any],
        ranked: Dict[str, List[int]],
        scores: Dict[str, List[float]],
        stage_ms: Dict[str, Dict[str, float]],
        extras: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> str:
        ks, mode, max_k = self.s["ks"], self.s["match_mode"], max(self.s["ks"])
        rows: Dict[str, Dict[str, Any]] = {}
        for q in self.queries:
            top = [self.views[i] for i in ranked[q.query_id][:max_k]]
            s = scores[q.query_id]
            row: Dict[str, Any] = {
                "query_id": q.query_id,
                "group": q.group,
                "answerable": q.answerable,
                "slices": list(q.slices),
                "top1_score": s[0] if s else None,
                "margin": (s[0] - s[1]) if len(s) > 1 else None,
                "top1_relevant": False,
            }
            if q.answerable:
                row["metrics"] = query_metrics(top, q, mode, ks)
                credit = credit_by_rank(top[:1], q.relevant, mode)
                row["top1_relevant"] = bool(credit and credit[0][1] > 0)
            row["total_ms"] = sum(stage[q.query_id] for stage in stage_ms.values() if q.query_id in stage)
            if extras and q.query_id in extras:
                row.update(extras[q.query_id])
            rows[q.query_id] = row

        answerable = [q for q in self.queries if q.answerable]
        ci = {}
        for metric in self.s["ci_metrics"]:
            values = [rows[q.query_id]["metrics"][metric] for q in answerable if metric in rows[q.query_id]["metrics"]]
            groups = [q.group for q in answerable if metric in rows[q.query_id]["metrics"]]
            ci[metric] = bootstrap_ci(values, groups, resamples=self.s["resamples"], seed=self.s["seed"])
        latency = {name: latency_summary(list(stage.values())) for name, stage in stage_ms.items()}
        latency["total"] = latency_summary([row["total_ms"] for row in rows.values()])
        config_id = short_hash(record["config"])
        # Share of answerable questions without any relevant evidence among the candidates (the first `depth`
        # for a reranked configuration, else the first max(ks) of the ranking): nothing downstream can rescue them.
        misses = [row["candidate_miss"] for row in rows.values() if row.get("candidate_miss") is not None]
        if not misses:
            hit_key = f"hit@{max_k}"
            misses = [1.0 - row["metrics"][hit_key] for row in rows.values() if "metrics" in row and hit_key in row["metrics"]]
        candidate_miss_rate = sum(misses) / len(misses) if misses else None
        summary = dict(
            record,
            config_id=config_id,
            candidate_miss_rate=candidate_miss_rate,
            metrics=aggregate({qid: row["metrics"] for qid, row in rows.items() if "metrics" in row}, self.queries),
            ci=ci,
            latency=latency,
            total_p95_ms=latency["total"]["p95_ms"],
        )
        with (self.run_dir / "summary.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(summary, ensure_ascii=False, default=_json_default) + "\n")
        with (self.run_dir / "per_query" / f"{config_id}.jsonl").open("w", encoding="utf-8") as f:
            for row in rows.values():
                f.write(json.dumps(row, ensure_ascii=False, default=_json_default) + "\n")
        self.count += 1
        print(f"[bench] {self.count} {config_id} recall@10={summary['metrics'].get('all', {}).get('recall@10')}")
        return config_id


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def run(cfg: Dict[str, Any]) -> Path:
    cfg, generation = conditions.apply_generation(cfg)
    summary_plan = plan(cfg)
    limit = int(cfg.get("max_evaluations", 500))
    if summary_plan["evaluations"] > limit:
        raise ValueError(f"{summary_plan['evaluations']} evaluations exceed max_evaluations={limit}; narrow the grid")
    queries = load_eval_set(cfg["eval_set"], split=cfg.get("split", "dev"))
    if not queries:
        raise ValueError("no queries in the selected split")
    match_mode = cfg.get("match_mode", "evidence")
    check_match_mode(queries, match_mode)
    ks = sorted({int(k) for k in cfg.get("ks", DEFAULT_KS)})
    candidates = cfg.get("candidates", {})
    dense_top = int(candidates.get("dense_top", 100))
    lexical_top = int(candidates.get("lexical_top", 100))
    fused_top = max(max(ks), int(candidates.get("fused_top", 100)))
    boot = cfg.get("bootstrap", {})
    cache_dir = Path(cfg.get("cache_dir") or Path(config.RUNS_DIR) / "bench_cache")
    out_root = Path(cfg.get("output_dir") or Path(config.RUNS_DIR) / "bench")
    run_dir = out_root / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{short_hash(cfg)}"
    split = cfg.get("split", "dev")
    eval_set_sha256 = file_fingerprint(cfg["eval_set"])
    # The held-out split is claimed before anything is written or computed (see eval/bench/holdout.py).
    test_claim = holdout.claim_test_split(cfg, eval_set_sha256, run_id=run_dir.name) if split == "test" else None
    (run_dir / "per_query").mkdir(parents=True, exist_ok=False)
    meta = {
        "schema": RUN_SCHEMA,
        "config": cfg,
        "plan": summary_plan,
        "split": split,
        "test_claim": test_claim,
        "tie_break": "chunk_id",
        "generation": generation,
        "required_metrics_missing": conditions.missing_required_metrics(ks),
        "eval_set_sha256": eval_set_sha256,
        "git_commit": _git_commit(),
        "queries": len(queries),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    (run_dir / "run.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    g = grid(cfg)
    scorers: Dict[Tuple[str, int], CrossEncoderScorer] = {}
    corpus_hashes: set = set()
    for chunk_cfg in g["chunking"]:
        chunks = corpus_mod.build_chunks(chunk_cfg, cfg["corpus"]["chunks_jsonl"], cfg["corpus"].get("documents_jsonl"))
        if not chunks:
            raise ValueError(f"chunking {chunk_cfg} produced no chunks")
        views = [RankedChunk(c.chunk_id, c.doc_id, c.text_key) for c in chunks]
        chunk_ids = [c.chunk_id for c in chunks]
        corpus_sha256 = corpus_mod.corpus_fingerprint(chunks)
        corpus_hashes.add(corpus_sha256)
        tenants = [c.tenant_id for c in chunks]
        writer = _Writer(
            run_dir,
            queries,
            views,
            {
                "ks": ks,
                "match_mode": match_mode,
                "ci_metrics": cfg.get("ci_metrics", list(DEFAULT_CI_METRICS)),
                "resamples": int(boot.get("resamples", 2000)),
                "seed": int(boot.get("seed", 0)),
            },
        )
        lexical_runs = [_run_lexical(chunks, spec, queries, lexical_top) for spec in g["lexical"]]
        for emb_cfg in g["embedding"]:
            emb = EmbeddingSpec(**emb_cfg)
            corpus_vecs, encode_stats = encode(emb, [c.text for c in chunks], "document", cache_dir)
            query_vecs, _ = encode(emb, [q.query for q in queries], "query", cache_dir)
            embed_latency = single_query_latency(emb, [q.query for q in queries], int(cfg.get("query_latency_sample", 50)))
            embed_ms = {q.query_id: embed_latency["p50_ms"] for q in queries}
            for dim_spec in g["dimension"]:
                projection = Projection(dim_spec, corpus_vecs)
                corpus_m, query_m = projection.apply(corpus_vecs), projection.apply(query_vecs)
                reference = _exact_reference(corpus_m, query_m, tenants, queries, dense_top)
                for quant_cfg in g["quantization"]:
                    for backend_cfg in g["backend"]:
                        if not _valid_dense(backend_cfg, quant_cfg):
                            continue
                        dense = _run_dense(backend_cfg, quant_cfg, corpus_m, query_m, tenants, queries, dense_top)
                        dense_info = dict(dense["describe"], ann_recall=_ann_recall(dense["results"], reference, dense_top))
                        for lex in lexical_runs:
                            for fusion_cfg in g["fusion"]:
                                settings, k_rrf = _fusion_settings(fusion_cfg)
                                ranked, scores, fusion_ms = {}, {}, {}
                                for q in queries:
                                    started = time.perf_counter()
                                    order, fused = fuse_one(
                                        dense["results"][q.query_id], lex["results"][q.query_id], settings, k_rrf, ids=chunk_ids
                                    )
                                    fusion_ms[q.query_id] = (time.perf_counter() - started) * 1000.0
                                    ranked[q.query_id], scores[q.query_id] = order[:fused_top], fused[:fused_top]
                                base_config = {
                                    "chunking": chunk_cfg, "embedding": emb_cfg, "dimension": dim_spec,
                                    "quantization": quant_cfg, "backend": backend_cfg, "lexical": lex["spec"],
                                    "fusion": fusion_cfg, "candidates": candidates, "rerank": None,
                                }
                                stage_ms = {"embed": embed_ms, "dense": dense["ms"], "lexical": lex["ms"], "fusion": fusion_ms}
                                record = {
                                    "config": base_config,
                                    "index": {"dense": dense_info, "lexical": lex["describe"], "encode": encode_stats,
                                              "projection": {"spec": dim_spec, "explained_variance": getattr(projection, "explained_variance", None)},
                                              "corpus_sha256": corpus_sha256,
                                              "embedding_profile": conditions.embedding_profile(emb, int(corpus_m.shape[1]))},
                                    "memory_bytes": dense_info.get("memory_bytes_estimate"),
                                }
                                writer.evaluate(record, ranked, scores, stage_ms)
                                _run_reranks(cfg, g["rerank"], scorers, cache_dir, chunks, queries, writer, record, ranked, scores, stage_ms)
    meta["finished_at"] = datetime.now(timezone.utc).isoformat()
    meta["corpus_sha256"] = sorted(corpus_hashes)
    (run_dir / "run.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return run_dir


def _run_reranks(cfg, rerank_cfgs, scorers, cache_dir, chunks, queries, writer, record, ranked, scores, stage_ms) -> None:
    for rr in rerank_cfgs:
        if not rr.get("model"):
            continue
        key = (rr["model"], int(rr.get("max_length", 512)))
        if key not in scorers:
            scorers[key] = CrossEncoderScorer(rr["model"], cache_dir, max_length=key[1], batch_size=int(rr.get("batch_size", 32)))
        scorer = scorers[key]
        depths = sorted({int(d) for d in _as_list(rr.get("depths", [30]))})
        rerank_scores = {
            q.query_id: scorer.scores(q.query, [chunks[i].text for i in ranked[q.query_id][: max(depths)]]) for q in queries
        }
        per_pair_ms = scorer.seconds_per_pair() * 1000.0
        for depth in depths:
            new_ranked, new_scores, rerank_ms, extras = {}, {}, {}, {}
            for q in queries:
                base = ranked[q.query_id]
                head, s = base[:depth], rerank_scores[q.query_id][:depth]
                order = sorted(range(len(head)), key=lambda i: (-s[i], i))
                new_ranked[q.query_id] = [head[i] for i in order] + base[depth:]
                new_scores[q.query_id] = [s[i] for i in order]
                rerank_ms[q.query_id] = len(head) * per_pair_ms
                base_scores = scores[q.query_id]
                # The un-reranked row of the same pipeline is in the base
                # config's per-query file; calibrate pairs the two by query_id.
                extras[q.query_id] = {
                    "fused_margin": (base_scores[0] - base_scores[1]) if len(base_scores) > 1 else None,
                    "candidate_depth": depth,
                    "candidate_miss": conditions.candidate_miss([writer.views[i] for i in head], q, writer.s["match_mode"]),
                }
            rerank_record = dict(record, config=dict(record["config"], rerank=dict(rr, depth=depth)))
            stages = dict(stage_ms, rerank_estimated=rerank_ms)
            writer.evaluate(rerank_record, new_ranked, new_scores, stages, extras)
