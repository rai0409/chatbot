from __future__ import annotations

import logging
import sys

import pytest

import config
from rag_core import cross_encoder_reranker, qa
from rag_core.retrieval import RetrievedChunk


def _chunk(chunk_id: str, text: str, score: float = 0.5, **meta) -> RetrievedChunk:
    return RetrievedChunk(
        text=text,
        metadata={"id": chunk_id, "source_doc": "doc.pdf", **meta},
        score=score,
    )


@pytest.fixture(autouse=True)
def _reset_module_state():
    cross_encoder_reranker._get_cross_encoder_model.cache_clear()
    cross_encoder_reranker._SCORING_FAILURE_WARNED = False
    yield
    cross_encoder_reranker._get_cross_encoder_model.cache_clear()
    cross_encoder_reranker._SCORING_FAILURE_WARNED = False


class _FakeCrossEncoder:
    def __init__(self, scores):
        self.scores = list(scores)
        self.pairs = []

    def predict(self, pairs):
        self.pairs.extend(pairs)
        return self.scores[: len(pairs)]


def test_knob_defaults_to_disabled():
    assert config.CROSS_ENCODER_RERANK_ENABLED is False


def test_enabled_reorders_top_n_and_preserves_tail(monkeypatch):
    fake = _FakeCrossEncoder(scores=[0.1, 0.9])
    monkeypatch.setattr(cross_encoder_reranker, "_get_cross_encoder_model", lambda name: fake)
    monkeypatch.setattr(config, "CROSS_ENCODER_TOP_N", 2)
    chunks = [
        _chunk("a", "本文A"),
        _chunk("b", "本文B"),
        _chunk("tail-1", "本文C"),
        _chunk("tail-2", "本文D"),
    ]

    result = cross_encoder_reranker.rerank("質問です", chunks)

    assert [c.metadata["id"] for c in result] == ["b", "a", "tail-1", "tail-2"]
    assert result[0].metadata["cross_encoder_score"] == 0.9
    assert result[1].metadata["cross_encoder_score"] == 0.1
    # Tail candidates are never scored.
    assert "cross_encoder_score" not in result[2].metadata
    assert "cross_encoder_score" not in result[3].metadata
    # Existing metadata is preserved.
    assert result[0].metadata["source_doc"] == "doc.pdf"


def test_scoring_pairs_prefer_searchable_text(monkeypatch):
    fake = _FakeCrossEncoder(scores=[0.5, 0.5])
    monkeypatch.setattr(cross_encoder_reranker, "_get_cross_encoder_model", lambda name: fake)
    monkeypatch.setattr(config, "CROSS_ENCODER_TOP_N", 2)
    chunks = [
        _chunk("a", "本文A", searchable_text="検索用テキストA"),
        _chunk("b", "本文B"),
    ]

    cross_encoder_reranker.rerank("質問です", chunks)

    assert fake.pairs == [("質問です", "検索用テキストA"), ("質問です", "本文B")]


def test_tie_scores_keep_input_order(monkeypatch):
    fake = _FakeCrossEncoder(scores=[0.5, 0.5, 0.5])
    monkeypatch.setattr(cross_encoder_reranker, "_get_cross_encoder_model", lambda name: fake)
    monkeypatch.setattr(config, "CROSS_ENCODER_TOP_N", 3)
    chunks = [_chunk("a", "本文A"), _chunk("b", "本文B"), _chunk("c", "本文C")]

    result = cross_encoder_reranker.rerank("質問です", chunks)

    assert [c.metadata["id"] for c in result] == ["a", "b", "c"]


def test_model_failure_warns_once_and_keeps_order(monkeypatch, caplog):
    def _boom(name):
        raise RuntimeError("model load failed")

    monkeypatch.setattr(cross_encoder_reranker, "_get_cross_encoder_model", _boom)
    chunks = [_chunk("a", "本文A"), _chunk("b", "本文B")]

    with caplog.at_level(logging.WARNING, logger="rag_core.cross_encoder_reranker"):
        first = cross_encoder_reranker.rerank("質問です", chunks)
        second = cross_encoder_reranker.rerank("質問です", chunks)

    assert [c.metadata["id"] for c in first] == ["a", "b"]
    assert [c.metadata["id"] for c in second] == ["a", "b"]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "cross-encoder rerank unavailable" in warnings[0].getMessage()


def test_missing_sentence_transformers_raises_clear_runtime_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)

    with pytest.raises(RuntimeError) as exc_info:
        cross_encoder_reranker._load_cross_encoder_class()

    message = str(exc_info.value)
    assert "sentence-transformers" in message
    assert "CROSS_ENCODER_RERANK_ENABLED" in message


def test_empty_candidates_pass_through(monkeypatch):
    monkeypatch.setattr(
        cross_encoder_reranker,
        "_get_cross_encoder_model",
        lambda name: pytest.fail("model must not load for empty input"),
    )

    assert cross_encoder_reranker.rerank("質問です", []) == []


# --- qa pipeline wiring ----------------------------------------------------


def _run_retrieve_and_rerank(monkeypatch, calls, *, ce_enabled):
    chunks = [_chunk("a", "本文A"), _chunk("b", "本文B")]

    def _fake_hybrid(question, client, top_k, **kwargs):
        return list(chunks)

    def _fake_heuristic(question, candidates, intent="other"):
        calls.append("rerank_chunks")
        return list(candidates)

    def _fake_cross_encoder(question, candidates):
        calls.append("cross_encoder")
        return list(reversed(candidates))

    def _fake_expand(candidates, **kwargs):
        calls.append("expand_parent")
        return list(candidates)

    monkeypatch.setattr(config, "CROSS_ENCODER_RERANK_ENABLED", ce_enabled)
    monkeypatch.setattr(qa, "hybrid_retrieve", _fake_hybrid)
    monkeypatch.setattr(qa, "rerank_chunks", _fake_heuristic)
    monkeypatch.setattr(qa, "cross_encoder_rerank", _fake_cross_encoder)
    monkeypatch.setattr(qa, "expand_parent_chunks", _fake_expand)

    return qa._retrieve_and_rerank(
        "質問です",
        "質問です",
        scoring_query="質問です",
        client=None,
        top_k=5,
        intent="other",
        query_type="other",
        tenant_id="default",
    )


def test_pipeline_disabled_by_default_does_not_invoke_stage(monkeypatch):
    calls = []
    _run_retrieve_and_rerank(monkeypatch, calls, ce_enabled=False)

    assert "cross_encoder" not in calls
    assert calls == ["rerank_chunks", "expand_parent"]


def test_pipeline_enabled_runs_after_heuristic_before_parent_expansion(monkeypatch):
    calls = []
    _, child_ranked, _ = _run_retrieve_and_rerank(monkeypatch, calls, ce_enabled=True)

    assert calls == ["rerank_chunks", "cross_encoder", "expand_parent"]
    # The cross-encoder's reordering reaches the downstream stages.
    assert [c.metadata["id"] for c in child_ranked] == ["b", "a"]


# --- eval runner mode -------------------------------------------------------


def test_eval_runner_exposes_hybrid_rerank_ce_mode(monkeypatch):
    from eval import runner

    assert "hybrid_rerank_ce" in runner._RETRIEVAL_MODES
    assert config.CROSS_ENCODER_RERANK_ENABLED is False
    with runner._retrieval_mode_runtime("hybrid_rerank_ce"):
        assert config.CROSS_ENCODER_RERANK_ENABLED is True
        # Heuristic rerank stays active in this mode (it is not stubbed out).
        assert qa.rerank_chunks is not None
    assert config.CROSS_ENCODER_RERANK_ENABLED is False
