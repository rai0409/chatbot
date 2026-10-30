"""Retrieval metrics against graded, possibly multi-item relevance.

Each relevant evidence item is credited once, at the first rank that
matches it; a chunk matching several items gains the highest of their
grades. Hence recall@k is the share of evidence items found, and nDCG never
double-counts several chunks of one relevant document.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from eval.bench.dataset import EvalQuery, Relevant, evidence_key


@dataclass(frozen=True)
class RankedChunk:
    chunk_id: str
    doc_id: str
    text_key: str  # evidence_key(chunk text), precomputed once per chunk


def _matches(chunk: RankedChunk, rel: Relevant, mode: str) -> bool:
    if mode == "chunk":
        return chunk.chunk_id == rel.chunk_id
    if mode == "doc":
        return chunk.doc_id == rel.doc_id
    key = evidence_key(rel.evidence or "")
    return bool(key) and key in chunk.text_key


def credit_by_rank(ranked: Sequence[RankedChunk], relevant: Sequence[Relevant], mode: str) -> List[Tuple[int, int]]:
    """(gain grade, number of newly matched items) for every rank."""
    matched = [False] * len(relevant)
    out: List[Tuple[int, int]] = []
    for chunk in ranked:
        best, new = 0, 0
        for i, rel in enumerate(relevant):
            if not matched[i] and _matches(chunk, rel, mode):
                matched[i] = True
                new += 1
                best = max(best, rel.grade)
        out.append((best, new))
    return out


def query_metrics(ranked: Sequence[RankedChunk], query: EvalQuery, mode: str, ks: Sequence[int]) -> Dict[str, float]:
    credits = credit_by_rank(ranked, query.relevant, mode)
    n_relevant = len(query.relevant)
    ideal = sorted((rel.grade for rel in query.relevant), reverse=True)
    first_hit = next((rank for rank, (_, new) in enumerate(credits, start=1) if new > 0), None)
    out: Dict[str, float] = {"first_relevant_rank": float(first_hit or 0)}
    for k in ks:
        top = credits[:k]
        found = sum(new for _, new in top)
        dcg = sum((2**gain - 1) / math.log2(rank + 1) for rank, (gain, _) in enumerate(top, start=1))
        idcg = sum((2**gain - 1) / math.log2(rank + 1) for rank, gain in enumerate(ideal[:k], start=1))
        out[f"recall@{k}"] = found / n_relevant if n_relevant else 0.0
        out[f"hit@{k}"] = 1.0 if found > 0 else 0.0
        out[f"mrr@{k}"] = 1.0 / first_hit if first_hit is not None and first_hit <= k else 0.0
        out[f"ndcg@{k}"] = dcg / idcg if idcg > 0 else 0.0
    return out


def aggregate(per_query: Dict[str, Dict[str, float]], queries: Sequence[EvalQuery]) -> Dict[str, Dict[str, float]]:
    """Mean of every metric overall and per slice (answerable queries only)."""
    buckets: Dict[str, List[Dict[str, float]]] = {"all": []}
    for query in queries:
        if not query.answerable or query.query_id not in per_query:
            continue
        row = per_query[query.query_id]
        buckets["all"].append(row)
        for tag in query.slices:
            buckets.setdefault(f"slice:{tag}", []).append(row)
    out: Dict[str, Dict[str, float]] = {}
    for name, rows in buckets.items():
        if not rows:
            continue
        keys = [key for key in rows[0] if key != "first_relevant_rank"]
        out[name] = {key: sum(row[key] for row in rows) / len(rows) for key in keys}
        out[name]["queries"] = float(len(rows))
    return out
