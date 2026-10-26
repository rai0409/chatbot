from __future__ import annotations

import pytest

import config
from rag_core import retrieval
from rag_core.retrieval import RetrievedChunk


def _vec(chunk_id, distance):
    return RetrievedChunk(
        text=chunk_id,
        metadata={"id": chunk_id, "vector_distance": distance},
        score=distance,
    )


def _kw(chunk_id, bm25, *, id_hits=0, rank=1):
    return RetrievedChunk(
        text=chunk_id,
        metadata={
            "id": chunk_id,
            "bm25_score": bm25,
            "exact_quoted_hits": 0,
            "exact_id_hits": id_hits,
            "exact_term_hits": id_hits,
        },
        score=0.25 + (rank - 1) * 0.02,
    )


def _patch_retrievers(monkeypatch, vector_hits, keyword_hits):
    monkeypatch.setattr(config, "ENABLE_HYBRID_RETRIEVAL", True)
    monkeypatch.setattr(retrieval, "vector_retrieve", lambda *a, **k: list(vector_hits))
    monkeypatch.setattr(retrieval, "keyword_retrieve", lambda *a, **k: list(keyword_hits))


def test_weighted_rrf_orders_by_lane_weights_and_exposes_ranks(monkeypatch):
    monkeypatch.setattr(config, "HYBRID_FUSION", "weighted_rrf")
    monkeypatch.setattr(config, "HYBRID_DENSE_WEIGHT", 1.0)
    monkeypatch.setattr(config, "HYBRID_KEYWORD_WEIGHT", 1.0)
    monkeypatch.setattr(config, "HYBRID_EXACT_WEIGHT", 2.0)
    _patch_retrievers(
        monkeypatch,
        [_vec("A", 0.1), _vec("B", 0.2)],
        [_kw("B", 5.0, rank=1), _kw("C", 3.0, id_hits=1, rank=2)],
    )

    out = retrieval.hybrid_retrieve("ZZ-10 の確認", None, top_k=3, rrf_k=10)

    assert [ch.metadata["id"] for ch in out] == ["C", "B", "A"]
    scores = {ch.metadata["id"]: ch.metadata["rrf_score"] for ch in out}
    assert scores["C"] == pytest.approx(1 / 12 + 2 / 11)
    assert scores["B"] == pytest.approx(1 / 12 + 1 / 11)
    assert scores["A"] == pytest.approx(1 / 11)
    by_id = {ch.metadata["id"]: ch.metadata for ch in out}
    assert by_id["B"]["retrieval_source"] == "hybrid"
    assert by_id["B"]["fusion_dense_rank"] == 2
    assert by_id["B"]["fusion_keyword_rank"] == 1
    assert by_id["C"]["fusion_exact_rank"] == 1
    assert by_id["C"]["fusion_method"] == "weighted_rrf"


def test_former_score_boost_fields_are_gone(monkeypatch):
    monkeypatch.setattr(config, "HYBRID_FUSION", "weighted_rrf")
    _patch_retrievers(monkeypatch, [_vec("A", 0.1)], [_kw("A", 9.0)])

    out = retrieval.hybrid_retrieve("質問", None, top_k=1, rrf_k=60)

    for key in (
        "keyword_evidence_boost",
        "vector_without_keyword_penalty",
        "page_anchor_boost",
        "page_evidence_boost",
    ):
        assert key not in out[0].metadata


def test_dbsf_combines_normalized_lane_scores(monkeypatch):
    monkeypatch.setattr(config, "HYBRID_FUSION", "dbsf")
    monkeypatch.setattr(config, "HYBRID_DENSE_WEIGHT", 1.0)
    monkeypatch.setattr(config, "HYBRID_KEYWORD_WEIGHT", 1.0)
    monkeypatch.setattr(config, "HYBRID_EXACT_WEIGHT", 0.0)
    _patch_retrievers(
        monkeypatch,
        [_vec("A", 0.1), _vec("B", 0.3)],
        [_kw("B", 4.0, rank=1), _kw("C", 2.0, rank=2)],
    )

    out = retrieval.hybrid_retrieve("質問", None, top_k=3, rrf_k=60)

    # dense similarity A=0.9, B=0.7 -> 2/3, 1/3; BM25 B=4, C=2 -> 2/3, 1/3
    assert [ch.metadata["id"] for ch in out] == ["B", "A", "C"]
    scores = {ch.metadata["id"]: ch.metadata["rrf_score"] for ch in out}
    assert scores["B"] == pytest.approx(1 / 3 + 2 / 3)
    assert scores["A"] == pytest.approx(2 / 3)
    assert scores["C"] == pytest.approx(1 / 3)


def test_dbsf_normalize_degenerate_inputs():
    assert retrieval._dbsf_normalize({}) == {}
    assert retrieval._dbsf_normalize({"a": 3.0}) == {"a": 0.5}
    assert retrieval._dbsf_normalize({"a": 2.0, "b": 2.0}) == {"a": 0.5, "b": 0.5}


def test_unknown_fusion_method_is_rejected(monkeypatch):
    monkeypatch.setattr(config, "HYBRID_FUSION", "linear")
    _patch_retrievers(monkeypatch, [], [])

    with pytest.raises(ValueError, match="Unknown HYBRID_FUSION"):
        retrieval.hybrid_retrieve("質問", None, top_k=3)
