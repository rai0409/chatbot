"""Optional cross-encoder rerank stage for fused retrieval candidates.

Off by default (CROSS_ENCODER_RERANK_ENABLED). Runs after the heuristic
rerank and keyword boost on already tenant-filtered candidates, before
parent expansion. Promotion to any default requires comparing the
hybrid_rerank vs hybrid_rerank_ce eval modes on a stamped real-vector
collection through eval/rerank_promotion_gate.py.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import List, Sequence

import config
from rag_core.retrieval import RetrievedChunk

logger = logging.getLogger(__name__)

_SCORING_FAILURE_WARNED = False


def _load_cross_encoder_class():
    try:
        from sentence_transformers import CrossEncoder
    except Exception as exc:
        raise RuntimeError(
            "Cross-encoder rerank requires sentence-transformers. "
            "Install it or disable CROSS_ENCODER_RERANK_ENABLED."
        ) from exc
    return CrossEncoder


@lru_cache(maxsize=2)
def _get_cross_encoder_model(model_name: str):
    cross_encoder = _load_cross_encoder_class()
    return cross_encoder(model_name)


def _pair_text(chunk: RetrievedChunk) -> str:
    meta = chunk.metadata or {}
    searchable = str(meta.get("searchable_text") or "").strip()
    return searchable or str(chunk.text or "")


def _warn_scoring_failure_once(exc: Exception) -> None:
    global _SCORING_FAILURE_WARNED
    if _SCORING_FAILURE_WARNED:
        return
    _SCORING_FAILURE_WARNED = True
    logger.warning(
        "cross-encoder rerank unavailable; keeping input order "
        "(model=%s error_type=%s)",
        str(getattr(config, "CROSS_ENCODER_MODEL", "")),
        type(exc).__name__,
    )


def rerank(question: str, chunks: Sequence[RetrievedChunk]) -> List[RetrievedChunk]:
    ordered = list(chunks)
    top_n = max(0, int(getattr(config, "CROSS_ENCODER_TOP_N", 20)))
    head = ordered[:top_n]
    if not head:
        return ordered
    try:
        model = _get_cross_encoder_model(str(config.CROSS_ENCODER_MODEL))
        scores = [float(s) for s in model.predict([(question, _pair_text(ch)) for ch in head])]
    except Exception as exc:
        _warn_scoring_failure_once(exc)
        return ordered
    for chunk, score in zip(head, scores):
        if chunk.metadata is None:
            chunk.metadata = {}
        chunk.metadata["cross_encoder_score"] = score
    # Stable sort: ties keep their pre-cross-encoder relative order.
    reordered = [chunk for chunk, _ in sorted(zip(head, scores), key=lambda pair: pair[1], reverse=True)]
    return reordered + ordered[top_n:]
