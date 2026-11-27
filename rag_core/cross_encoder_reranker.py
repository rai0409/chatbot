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

2026-11-22: the model is pinned with CROSS_ENCODER_REVISION (trust_remote_code
is always False); the loader cache key includes max_length and revision.
Pairs are scored in CROSS_ENCODER_BATCH_SIZE batches under the
CROSS_ENCODER_TIMEOUT_MS budget, and the final order blends the normalized
cross-encoder score with the keyword / profile boosts
(CROSS_ENCODER_BOOST_WEIGHT). warmup() loads the model; the web app should
call qa.warmup_models() at start-up.
"""

from __future__ import annotations

import logging
import time
from contextvars import ContextVar
from typing import Any, Dict, List, Optional, Sequence, Tuple

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


def _settings_key() -> tuple:
    max_length = int(getattr(config, "CROSS_ENCODER_MAX_LENGTH", 0) or 0)
    revision = str(getattr(config, "CROSS_ENCODER_REVISION", "") or "").strip()
    return max_length, revision


@load_once(maxsize=4)
def _load_cross_encoder(model_name: str, max_length: int = 0, revision: str = ""):
    # Cache key = (model, max_length, revision): changing either loads a new model.
    cross_encoder = _load_cross_encoder_class()
    from rag_core.embedding_provider import apply_num_threads

    apply_num_threads()
    kwargs = {"trust_remote_code": False}
    if max_length > 0:
        kwargs["max_length"] = max_length
    if revision:
        kwargs["revision"] = revision
    return cross_encoder(model_name, **kwargs)


def _get_cross_encoder_model(model_name: str):
    return _load_cross_encoder(model_name, *_settings_key())


_get_cross_encoder_model.cache_clear = lambda: _load_cross_encoder.cache_clear()  # type: ignore[attr-defined]


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


_LAST_RUN: ContextVar[Optional[Dict[str, Any]]] = ContextVar("cross_encoder_last_run", default=None)


def last_run() -> Optional[Dict[str, Any]]:
    """What the last rerank() in this context did (for the request trace), or None."""
    value = _LAST_RUN.get()
    return dict(value) if value else None


def reset_last_run() -> None:
    _LAST_RUN.set(None)


def _boost_delta(chunk: RetrievedChunk) -> float:
    """Distance the keyword and profile boosts took off this chunk (positive = promoted)."""
    details = (chunk.metadata or {}).get("score_details") or {}
    if not isinstance(details, dict):
        return 0.0
    delta = float(details.get("keyword_boost_value") or 0.0)
    profile = details.get("profile_boost")
    if isinstance(profile, dict):
        delta += float(profile.get("delta") or 0.0)
    return delta


def _predict_with_budget(model, question: str, head: Sequence[RetrievedChunk]) -> Tuple[List[float], bool]:
    """Scores of a prefix of head, in batches; stops when CROSS_ENCODER_TIMEOUT_MS is used up.

    The first batch always runs (a budget smaller than one batch would never
    rerank); the unscored remainder keeps its pre-cross-encoder order."""
    batch_size = max(1, int(getattr(config, "CROSS_ENCODER_BATCH_SIZE", 16) or 16))
    budget_ms = max(0, int(getattr(config, "CROSS_ENCODER_TIMEOUT_MS", 0) or 0))
    started = time.perf_counter()
    scores: List[float] = []
    for start in range(0, len(head), batch_size):
        if start and budget_ms and (time.perf_counter() - started) * 1000.0 >= budget_ms:
            return scores, True
        pairs = [(question, _pair_text(ch)) for ch in head[start:start + batch_size]]
        scores.extend(float(s) for s in model.predict(pairs))
    return scores, False


def _normalize(scores: Sequence[float]) -> List[float]:
    if not scores:
        return []
    low, high = min(scores), max(scores)
    if high - low <= 1e-12:
        return [0.0 for _ in scores]
    return [(s - low) / (high - low) for s in scores]


def rerank(question: str, chunks: Sequence[RetrievedChunk]) -> List[RetrievedChunk]:
    """Reorder the top CROSS_ENCODER_TOP_N candidates.

    final = min-max normalized cross-encoder score + CROSS_ENCODER_BOOST_WEIGHT
    x (keyword boost + profile boost delta), so the boosts applied before this
    stage are not discarded. RetrievedChunk.score is rewritten to follow the
    new order (the head's own scores, ascending, assigned by new rank), so a
    later score sort (PREFER_SORT_MODE=legacy) keeps the reranked order."""
    global _SCORING_FAILURES, _SKIPPED
    _LAST_RUN.set(None)
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
            _LAST_RUN.set({"status": "skipped_margin", "margin": round(margin, 6)})
            return ordered
    started = time.perf_counter()
    try:
        model = _get_cross_encoder_model(str(config.CROSS_ENCODER_MODEL))
        scores, budget_exceeded = _predict_with_budget(model, question, head)
    except Exception as exc:
        _SCORING_FAILURES += 1
        _warn_scoring_failure_once(exc)
        _LAST_RUN.set({"status": "failed", "error_type": type(exc).__name__})
        return ordered
    scored, unscored = head[: len(scores)], head[len(scores):]
    weight = float(getattr(config, "CROSS_ENCODER_BOOST_WEIGHT", 0.0) or 0.0)
    normalized = _normalize(scores)
    ranked: List[Tuple[float, int, RetrievedChunk]] = []
    for idx, (chunk, raw, norm) in enumerate(zip(scored, scores, normalized)):
        delta = _boost_delta(chunk)
        final = norm + weight * delta
        meta = dict(chunk.metadata or {})
        meta["cross_encoder_score"] = raw
        meta["cross_encoder_final"] = round(final, 6)
        meta["score_before_cross_encoder"] = float(chunk.score)
        ranked.append((final, idx, RetrievedChunk(text=chunk.text, metadata=meta, score=float(chunk.score))))
    # Stable: ties keep their pre-cross-encoder relative order.
    ranked.sort(key=lambda item: (-item[0], item[1]))
    new_head = [item[2] for item in ranked] + [
        RetrievedChunk(text=ch.text, metadata=dict(ch.metadata or {}), score=float(ch.score)) for ch in unscored
    ]
    for chunk, score in zip(new_head, sorted(float(ch.score) for ch in head)):
        chunk.score = score
    _LAST_RUN.set({
        "status": "partial_budget" if budget_exceeded else "ok",
        "scored": len(scores),
        "unscored_kept_order": len(unscored),
        "boost_weight": weight,
        "ms": int((time.perf_counter() - started) * 1000),
    })
    return new_head + ordered[top_n:]
