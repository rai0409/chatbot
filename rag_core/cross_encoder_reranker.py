"""Optional cross-encoder rerank stage for fused retrieval candidates.

Off by default (CROSS_ENCODER_RERANK_ENABLED). Runs after the heuristic
rerank and keyword boost on already tenant-filtered candidates, before
parent expansion. Promotion to any default requires comparing the
hybrid_rerank vs hybrid_rerank_ce eval modes on a stamped real-vector
collection through eval/rerank_promotion_gate.py.

The default model is cl-nagoya/ruri-v3-reranker-310m (Apache-2.0, max 8192
tokens). Its model card loads it with sentence-transformers' CrossEncoder and
scores [query, passage] pairs with predict(), which is the call used here.
CROSS_ENCODER_MAX_LENGTH caps the pair length: cost grows with input length,
and children are reranked before parent expansion, so long inputs are rare.

CROSS_ENCODER_SKIP_MARGIN turns the stage into a cascade: when the fused
scores of the two best candidates differ by at least the margin, the order
is already confident and the model call is skipped.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Sequence

import config
from rag_core.model_cache import load_once
from rag_core.retrieval import RetrievedChunk

logger = logging.getLogger(__name__)

_SCORING_FAILURE_WARNED = False
_SCORING_FAILURES = 0
_SKIPPED = 0


def _load_cross_encoder_class():
    try:
        from sentence_transformers import CrossEncoder
    except Exception as exc:
        raise RuntimeError(
            "Cross-encoder rerank requires sentence-transformers. "
            "Install it or disable CROSS_ENCODER_RERANK_ENABLED."
        ) from exc
    return CrossEncoder


@load_once(maxsize=2)
def _get_cross_encoder_model(model_name: str):
    cross_encoder = _load_cross_encoder_class()
    max_length = int(getattr(config, "CROSS_ENCODER_MAX_LENGTH", 0) or 0)
    if max_length > 0:
        return cross_encoder(model_name, max_length=max_length)
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


def scoring_failure_count() -> int:
    """Rerank calls that fell back to input order; export it as a metric."""
    return _SCORING_FAILURES


def skipped_count() -> int:
    """Rerank calls skipped by CROSS_ENCODER_SKIP_MARGIN; export it as a metric."""
    return _SKIPPED


def _fused_margin(head: Sequence[RetrievedChunk]) -> Optional[float]:
    # Heuristic reranking may have reordered the head, so take the two
    # largest fused scores rather than the first two positions; this matches
    # the fused top1-top2 margin measured by `python -m eval.bench`.
    scores = sorted(
        (float(ch.metadata["rrf_score"]) for ch in head if ch.metadata and ch.metadata.get("rrf_score") is not None),
        reverse=True,
    )
    return scores[0] - scores[1] if len(scores) >= 2 else None


def warmup() -> bool:
    """Load the model ahead of the first request when rerank is enabled.

    Returns True when the model is ready. Failures follow the same
    keep-input-order policy as rerank() and are counted.
    """
    global _SCORING_FAILURES
    if not getattr(config, "CROSS_ENCODER_RERANK_ENABLED", False):
        return False
    try:
        _get_cross_encoder_model(str(config.CROSS_ENCODER_MODEL))
    except Exception as exc:
        _SCORING_FAILURES += 1
        _warn_scoring_failure_once(exc)
        return False
    return True


def rerank(question: str, chunks: Sequence[RetrievedChunk]) -> List[RetrievedChunk]:
    global _SCORING_FAILURES, _SKIPPED
    ordered = list(chunks)
    top_n = max(0, int(getattr(config, "CROSS_ENCODER_TOP_N", 30)))
    head = ordered[:top_n]
    if not head:
        return ordered
    skip_margin = float(getattr(config, "CROSS_ENCODER_SKIP_MARGIN", 0.0) or 0.0)
    if skip_margin > 0.0:
        margin = _fused_margin(head)
        if margin is not None and margin >= skip_margin:
            _SKIPPED += 1
            return ordered
    try:
        model = _get_cross_encoder_model(str(config.CROSS_ENCODER_MODEL))
        scores = [float(s) for s in model.predict([(question, _pair_text(ch)) for ch in head])]
    except Exception as exc:
        _SCORING_FAILURES += 1
        _warn_scoring_failure_once(exc)
        return ordered
    for chunk, score in zip(head, scores):
        if chunk.metadata is None:
            chunk.metadata = {}
        chunk.metadata["cross_encoder_score"] = score
    # Stable sort: ties keep their pre-cross-encoder relative order.
    reordered = [chunk for chunk, _ in sorted(zip(head, scores), key=lambda pair: pair[1], reverse=True)]
    return reordered + ordered[top_n:]
