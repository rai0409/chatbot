"""Candidate-limited, reproducible JQaRA dev evaluation.

JQaRA rows are *not* a retrieval corpus.  Every ``q_id`` owns its supplied
candidate passages and all ranking, BM25 statistics, and relevance metrics
are calculated within that one set only.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import random
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from numbers import Integral
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from eval.bench.corpus import Chunk
from eval.bench.encoders import DenseIndex, EmbeddingSpec, encode, load_model
from eval.bench.lexical import LexicalHit, LexicalIndex, LexicalSpec
from eval.bench.metrics import RankedChunk, query_metrics
from eval.bench.rerank_cache import CrossEncoderScorer
from eval.bench.runner import _fusion_settings, fuse_one
from eval.bench.stats import bootstrap_ci, paired_bootstrap_diff


SCHEMA = "jqara-candidate-eval-v1"
REQUIRED_COLUMNS = {
    "id": "string", "q_id": "string", "passage_row_id": "string", "label": "int64",
    "text": "string", "title": "string", "question": "string", "answers": "list",
}
DEFAULT_KS = (1, 3, 5, 10, 20)
DEFAULT_OUTPUT = Path.home() / ".cache/chatbot/evaluations/jqara"


@dataclass(frozen=True)
class Candidate:
    id: str
    passage_row_id: str
    title: str
    text: str
    label: int
    answers: Tuple[str, ...]

    @property
    def searchable_text(self) -> str:
        """Title is indexed as a separate visible field, followed by text."""
        return f"{self.title}\n{self.text}"


@dataclass(frozen=True)
class JQaRAQuery:
    q_id: str
    question: str
    candidates: Tuple[Candidate, ...]

    @property
    def relevant_ids(self) -> Tuple[str, ...]:
        return tuple(c.passage_row_id for c in self.candidates if c.label == 1)


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _clean_string(value: Any, where: str, errors: List[str]) -> str:
    if not isinstance(value, str):
        errors.append(f"{where}: must be a string")
        return ""
    value = value.strip()
    if not value:
        errors.append(f"{where}: must not be empty")
    return value


def load_jqara(path: str | Path, *, expected_candidates: int = 50) -> List[JQaRAQuery]:
    """Load and strictly validate the documented JQaRA candidate contract."""
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - dependency error is environment specific
        raise RuntimeError("JQaRA Parquet support requires pyarrow") from exc
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"JQaRA input does not exist: {path}")
    schema = pq.read_schema(path)
    names = set(schema.names)
    missing = set(REQUIRED_COLUMNS) - names
    if missing:
        raise ValueError(f"JQaRA schema missing columns: {sorted(missing)}")
    for name, expected in REQUIRED_COLUMNS.items():
        actual = str(schema.field(name).type)
        if expected == "list":
            ok = actual.startswith("list<")
        else:
            ok = actual == expected
        if not ok:
            raise ValueError(f"JQaRA schema column {name!r} must be {expected}, got {actual}")

    grouped: Dict[str, List[Candidate]] = defaultdict(list)
    questions: Dict[str, str] = {}
    seen_global_ids, seen_per_query, errors = set(), defaultdict(set), []
    for row_no, row in enumerate(pq.ParquetFile(path).iter_batches(columns=list(REQUIRED_COLUMNS)), start=1):
        for item in row.to_pylist():
            where = f"row {row_no}"
            q_id = _clean_string(item["q_id"], f"{where}.q_id", errors)
            question = _clean_string(item["question"], f"{where}.question", errors)
            identifier = _clean_string(item["id"], f"{where}.id", errors)
            passage_id = _clean_string(item["passage_row_id"], f"{where}.passage_row_id", errors)
            title = _clean_string(item["title"], f"{where}.title", errors)
            text = _clean_string(item["text"], f"{where}.text", errors)
            label = item["label"]
            if isinstance(label, bool) or not isinstance(label, Integral) or label not in (0, 1):
                errors.append(f"{where}.label: must be integer 0 or 1")
            label = int(label) if isinstance(label, Integral) and not isinstance(label, bool) else label
            answers = item["answers"]
            if not isinstance(answers, list) or any(not isinstance(a, str) for a in answers):
                errors.append(f"{where}.answers: must be list[string]")
                answers = []
            if identifier in seen_global_ids:
                errors.append(f"{where}.id: duplicate source id {identifier!r}")
            seen_global_ids.add(identifier)
            if passage_id in seen_per_query[q_id]:
                errors.append(f"{where}.passage_row_id: duplicate within q_id {q_id!r}: {passage_id!r}")
            seen_per_query[q_id].add(passage_id)
            prior = questions.setdefault(q_id, question)
            if prior != question:
                errors.append(f"{where}.question: inconsistent question for q_id {q_id!r}")
            grouped[q_id].append(Candidate(identifier, passage_id, title, text, label, tuple(answers)))
    for q_id, candidates in grouped.items():
        if len(candidates) != expected_candidates:
            errors.append(f"q_id {q_id!r}: expected {expected_candidates} candidates, got {len(candidates)}")
    if errors:
        preview = "\n".join(errors[:20])
        raise ValueError(f"JQaRA data contract has {len(errors)} violation(s):\n{preview}")
    return [JQaRAQuery(q_id, questions[q_id], tuple(grouped[q_id])) for q_id in sorted(grouped)]


def create_split(queries: Sequence[JQaRAQuery], *, seed: int, tuning_share: float) -> Dict[str, Any]:
    if not 0.0 < tuning_share < 1.0:
        raise ValueError("tuning_share must be strictly between 0 and 1")
    # Exact normalized duplicates are assigned as a unit, preventing the most
    # direct question leakage. Similar-but-nonidentical questions are reported
    # as an audit limitation rather than guessed at by an opaque classifier.
    groups: Dict[str, List[str]] = defaultdict(list)
    for q in queries:
        key = "".join(q.question.split()).casefold()
        groups[key].append(q.q_id)
    keys = sorted(groups)
    random.Random(seed).shuffle(keys)
    target = round(len(queries) * tuning_share)
    tuning, validation = [], []
    for key in keys:
        destination = tuning if len(tuning) < target else validation
        destination.extend(groups[key])
    return {
        "schema": SCHEMA + "-split-v1", "seed": seed, "tuning_share": tuning_share,
        "tuning_q_ids": sorted(tuning), "validation_q_ids": sorted(validation),
        "exact_duplicate_question_groups": sum(1 for ids in groups.values() if len(ids) > 1),
        "similarity_leakage_check": "exact normalized question duplicates grouped; semantic similarity is not inferred",
    }


def _selected(queries: Sequence[JQaRAQuery], split: str, manifest: Optional[Dict[str, Any]], sample_size: Optional[int], seed: int) -> List[JQaRAQuery]:
    if split == "dev":
        selected = list(queries)
    else:
        if manifest is None:
            raise ValueError("split tuning/validation requires --split-manifest (create it with jqara-split)")
        ids = set(manifest[f"{split}_q_ids"])
        selected = [q for q in queries if q.q_id in ids]
    if sample_size is not None:
        if sample_size <= 0:
            raise ValueError("sample_size must be positive")
        random.Random(seed).shuffle(selected)
        selected = sorted(selected[:sample_size], key=lambda q: q.q_id)
    if not selected:
        raise ValueError("selected split has no questions")
    return selected


def _chunks(query: JQaRAQuery) -> List[Chunk]:
    return [Chunk(c.passage_row_id, c.id, "jqara", c.searchable_text, "") for c in query.candidates]


def _ranked(query: JQaRAQuery, order: Sequence[int]) -> List[RankedChunk]:
    return [RankedChunk(query.candidates[i].passage_row_id, query.candidates[i].id, "") for i in order]


def _query_for_metrics(query: JQaRAQuery):
    # Reuse standard metrics without converting JQaRA into a global corpus.
    from eval.bench.dataset import EvalQuery, Relevant
    return EvalQuery(query.q_id, query.question, "dev", "jqara", True,
                     tuple(Relevant(pid, None, None, 1) for pid in query.relevant_ids), (), query.q_id)


def _source_fingerprint() -> Dict[str, str]:
    root = Path(__file__).resolve().parents[2]
    files = ["eval/bench/jqara.py", "eval/bench/encoders.py", "eval/bench/lexical.py", "eval/bench/metrics.py",
             "eval/bench/stats.py", "eval/bench/runner.py", "eval/bench/rerank_cache.py"]
    values = {name: sha256_file(root / name) for name in files}
    values["combined"] = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
    return values


def _versions() -> Dict[str, str]:
    out = {"python": platform.python_version()}
    for package in ("numpy", "pyarrow", "sentence-transformers", "torch"):
        try:
            out[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            out[package] = "not-installed"
    return out


def _asset_info(path: Optional[str]) -> Optional[Dict[str, Any]]:
    if not path:
        return None
    root = Path(path)
    if not root.is_dir():
        raise ValueError(f"local_model_path is not a directory: {root}")
    configs = sorted(root.glob("*.json"))
    return {"path": str(root.resolve()), "config_sha256": {p.name: sha256_file(p) for p in configs}}


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def run(config: Dict[str, Any]) -> Path:
    data_path = Path(config["input"])
    queries = load_jqara(data_path, expected_candidates=int(config.get("expected_candidates", 50)))
    manifest = json.loads(Path(config["split_manifest"]).read_text(encoding="utf-8")) if config.get("split_manifest") else None
    selected = _selected(queries, config.get("split", "dev"), manifest, config.get("sample_size"), int(config.get("seed", 0)))
    method = config.get("method", "dense_only")
    aliases = {"dense": "dense_only", "bm25": "lexical_only", "rrf": "weighted_rrf"}
    method = aliases.get(method, method)
    if method not in ("dense_only", "lexical_only", "weighted_rrf", "dbsf"):
        raise ValueError("method must be dense_only, lexical_only (or bm25), weighted_rrf, or dbsf")
    ks = sorted(set(int(k) for k in config.get("ks", DEFAULT_KS)))
    if not ks or min(ks) <= 0:
        raise ValueError("metrics k values must be positive")
    needs_dense = method != "lexical_only"
    emb = EmbeddingSpec(**config["embedding"]) if needs_dense else None
    lexical = LexicalSpec(**config.get("bm25", {}))
    fusion, rrf_k = _fusion_settings(dict(config.get("fusion", {}), method=method))
    out_root = Path(config.get("output_dir") or DEFAULT_OUTPUT)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + hashlib.sha1(
        json.dumps(config, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:12]
    run_dir = out_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    cache_dir = out_root / "cache"
    max_k = min(50, max(ks))

    doc_vectors = query_vectors = None
    if emb:
        all_texts = [c.searchable_text for q in selected for c in q.candidates]
        doc_vectors, _ = encode(emb, all_texts, "document", cache_dir)
        query_vectors, _ = encode(emb, [q.question for q in selected], "query", cache_dir)
        if doc_vectors.shape[1] != query_vectors.shape[1]:
            raise ValueError("embedding query/document dimensions differ; refusing to mix vectors")
    rerank_cfg = config.get("reranker") or {}
    reranker = None
    if rerank_cfg.get("enabled"):
        if not rerank_cfg.get("model"):
            raise ValueError("reranker.enabled requires reranker.model")
        reranker = CrossEncoderScorer(rerank_cfg["model"], cache_dir,
            max_length=int(rerank_cfg.get("max_length", 512)), batch_size=int(rerank_cfg.get("batch_size", 32)),
            revision=rerank_cfg.get("revision"), local_model_path=rerank_cfg.get("local_model_path"))
    rows, offset = [], 0
    for q_index, query in enumerate(selected):
        chunks = _chunks(query)
        lex_hits = LexicalIndex(chunks, lexical).search(query.question, 50, "jqara")
        # BM25 legitimately has no posting for many candidates.  They still
        # belong to this question's fixed candidate ranking and must receive
        # deterministic zero scores rather than vanish from the evaluation.
        seen_lex = {hit.index for hit in lex_hits}
        lex_hits = lex_hits + [LexicalHit(i, 0.0, 0, 0, 0) for i in range(len(chunks)) if i not in seen_lex]
        dense_hits = []
        if emb:
            vectors = doc_vectors[offset:offset + len(chunks)]
            dense_hits = DenseIndex(vectors, ["jqara"] * len(chunks)).search(query_vectors[q_index], 50, "jqara")
        offset += len(chunks)
        order, scores = fuse_one(dense_hits, lex_hits, fusion, rrf_k)
        dense_order, dense_scores = (fuse_one(dense_hits, lex_hits, {"method": "dense_only"}, rrf_k) if emb else ([], []))
        if reranker:
            depth = min(int(rerank_cfg.get("depth", 30)), len(order))
            rerank_scores = reranker.scores(query.question, [chunks[i].text for i in order[:depth]])
            permutation = sorted(range(depth), key=lambda i: (-rerank_scores[i], i))
            order = [order[i] for i in permutation] + order[depth:]
            scores = [rerank_scores[i] for i in permutation] + scores[depth:]
        metrics = query_metrics(_ranked(query, order[:max_k]), _query_for_metrics(query), "chunk", ks)
        dense_metrics = query_metrics(_ranked(query, dense_order[:max_k]), _query_for_metrics(query), "chunk", ks) if emb else None
        positive_ranks = [rank for rank, idx in enumerate(order, 1) if query.candidates[idx].label == 1]
        dense_positions = {idx: rank for rank, idx in enumerate(dense_order, 1)}
        dense_score_by_index = {idx: score for idx, score in zip(dense_order, dense_scores)}
        for rank, (idx, score) in enumerate(zip(order, scores), 1):
            c = query.candidates[idx]
            rows.append({"query_id": query.q_id, "question": query.question, "method": method,
                         "candidate_id": c.passage_row_id, "source_id": c.id, "rank": rank, "label": c.label,
                         "score": float(score), "dense_rank": dense_positions.get(idx),
                         "dense_score": dense_score_by_index.get(idx), "positive_ranks": positive_ranks,
                         "metrics": metrics, "dense_metrics": dense_metrics})
    by_query = {q.q_id: next(r for r in rows if r["query_id"] == q.q_id) for q in selected}
    metric_queries = [q for q in selected if q.relevant_ids]
    summary_metrics: Dict[str, Any] = {
        "queries": len(selected), "ranking_metric_queries": len(metric_queries),
        "zero_positive_queries": len(selected) - len(metric_queries),
    }
    for name in (f"{m}@{k}" for m in ("recall", "mrr", "ndcg", "hit") for k in ks):
        values = [by_query[q.q_id]["metrics"][name] for q in metric_queries]
        summary_metrics[name] = bootstrap_ci(values, resamples=int(config.get("bootstrap_resamples", 2000)), seed=int(config.get("seed", 0)))
    if emb and method in ("weighted_rrf", "dbsf"):
        summary_metrics["hybrid_vs_dense"] = {
            name: (lambda counts: {"improved": counts["improved"], "degraded": counts["degraded"], "tied": counts["tied"]})(Counter(
                "improved" if by_query[q.q_id]["metrics"][name] > by_query[q.q_id]["dense_metrics"][name]
                else "degraded" if by_query[q.q_id]["metrics"][name] < by_query[q.q_id]["dense_metrics"][name] else "tied"
                for q in metric_queries
            )) for name in (f"recall@{k}" for k in ks)
        }
    metadata = {"schema": SCHEMA, "dataset": "JQaRA", "dataset_revision": config.get("dataset_revision"),
                "split": config.get("split", "dev"), "input": str(data_path.resolve()), "input_sha256": sha256_file(data_path),
                "questions": len(selected), "candidates_per_question": int(config.get("expected_candidates", 50)),
                "config": config, "embedding": (asdict(emb) if emb else None),
                "embedding_prefixes": ({"query": emb.prefixes()[0], "document": emb.prefixes()[1]} if emb else None),
                "embedding_dimensions": int(doc_vectors.shape[1]) if doc_vectors is not None else None,
                "embedding_normalization": "l2", "embedding_asset": _asset_info(emb.local_model_path) if emb else None,
                "reranker_asset": _asset_info(rerank_cfg.get("local_model_path")) if reranker else None,
                "source_sha256": _source_fingerprint(), "environment": _versions(),
                "metrics": {"definition": "standard IR; zero-positive questions remain in output and are reported separately, but are excluded from ranking metric denominators because Recall/MRR/nDCG are undefined without a relevant passage",
                            "ks": ks}, "bootstrap": {"seed": int(config.get("seed", 0)), "resamples": int(config.get("bootstrap_resamples", 2000))}}
    _write_json(run_dir / "config.json", config)
    _write_json(run_dir / "metadata.json", metadata)
    _write_json(run_dir / "summary.json", summary_metrics)
    with (run_dir / "per_query.jsonl").open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return run_dir


def validate(config: Dict[str, Any]) -> Dict[str, Any]:
    has_experiment = any(key in config for key in ("method", "embedding", "bm25", "fusion", "reranker"))
    method_value = config.get("method", "dense_only")
    method = {"dense": "dense_only", "bm25": "lexical_only", "rrf": "weighted_rrf"}.get(method_value, method_value)
    if method not in ("dense_only", "lexical_only", "weighted_rrf", "dbsf"):
        raise ValueError("method must be dense_only, lexical_only (or bm25), weighted_rrf, or dbsf")
    if has_experiment and method != "lexical_only" and not config.get("embedding", {}).get("model"):
        raise ValueError("dense/hybrid JQaRA evaluation requires embedding.model")
    if any(int(k) <= 0 for k in config.get("ks", DEFAULT_KS)):
        raise ValueError("metrics k values must be positive")
    reranker = config.get("reranker") or {}
    if reranker.get("enabled") and not reranker.get("model"):
        raise ValueError("reranker.enabled requires reranker.model")
    if config.get("embedding", {}).get("local_model_path"):
        _asset_info(config["embedding"]["local_model_path"])
    if reranker.get("local_model_path"):
        _asset_info(reranker["local_model_path"])
    queries = load_jqara(config["input"], expected_candidates=int(config.get("expected_candidates", 50)))
    return {"valid": True, "method": method if has_experiment else None, "questions": len(queries), "rows": sum(len(q.candidates) for q in queries),
            "zero_positive_queries": sum(not q.relevant_ids for q in queries), "input_sha256": sha256_file(config["input"])}


def compare(run_dirs: Sequence[str | Path], baseline: str | Path, *, metric: str, resamples: int, seed: int) -> Dict[str, Any]:
    def read(path):
        root = Path(path); meta = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
        heads = {}
        for line in (root / "per_query.jsonl").read_text(encoding="utf-8").splitlines():
            row = json.loads(line); heads.setdefault(row["query_id"], row)
        return root, meta, heads
    _, base_meta, base_rows = read(baseline)
    result = {"baseline": str(baseline), "metric": metric, "comparisons": []}
    for candidate in run_dirs:
        root, meta, rows = read(candidate)
        if meta["input_sha256"] != base_meta["input_sha256"] or set(rows) != set(base_rows):
            raise ValueError("paired comparison requires identical input fingerprint and query-id set")
        ids = sorted(rows)
        before = [base_rows[q]["metrics"][metric] for q in ids]
        after = [rows[q]["metrics"][metric] for q in ids]
        changed = Counter("improved" if a > b else "degraded" if a < b else "tied" for b, a in zip(before, after))
        result["comparisons"].append({"candidate": str(root), "paired_queries": len(ids),
                                      **paired_bootstrap_diff(before, after, resamples=resamples, seed=seed),
                                      "improved": changed["improved"], "degraded": changed["degraded"], "tied": changed["tied"]})
    return result
