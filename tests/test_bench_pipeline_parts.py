from __future__ import annotations

import math

import numpy as np
import pytest

from rag_core.bm25_index import InvertedBM25
from eval.bench.corpus import window_chunks
from eval.bench.encoders import DenseIndex, Projection
from eval.bench.lexical import LexicalHit
from eval.bench.runner import fuse_one, plan


def test_inverted_bm25_follows_okapi_formula():
    index = InvertedBM25([["a", "b"], ["a"], ["c"]], k1=1.2, b=0.75)
    idf = math.log(1.0 + (3 - 2 + 0.5) / (2 + 0.5))
    avg = 4 / 3

    scores = index.score(["a"])

    norm0 = 1.2 * (1 - 0.75 + 0.75 * 2 / avg)
    norm1 = 1.2 * (1 - 0.75 + 0.75 * 1 / avg)
    assert set(scores) == {0, 1}
    assert scores[0] == pytest.approx(idf * 2.2 / (1 + norm0))
    assert scores[1] == pytest.approx(idf * 2.2 / (1 + norm1))
    assert index.score(["zzz"]) == {}
    assert index.top_k(["a"], 1)[0][0] == 1  # shorter document scores higher


def test_window_chunks_respect_size_and_overlap():
    text = "一文目です。二文目です。三文目です。四文目です。"

    windows = window_chunks(text, chars=12, overlap=0.5)

    assert windows == ["一文目です。二文目です。", "二文目です。三文目です。", "三文目です。四文目です。"]


def test_fuse_one_weighted_rrf_matches_hand_computation():
    dense = [(0, 0.9), (1, 0.8)]
    lexical = [LexicalHit(1, 5.0, 0, 0, 1), LexicalHit(2, 3.0, 0, 1, 1)]
    settings = {"method": "weighted_rrf", "dense_weight": 1.0, "keyword_weight": 1.0, "exact_weight": 2.0}

    order, scores = fuse_one(dense, lexical, settings, 10)

    assert order == [2, 1, 0]
    assert scores[0] == pytest.approx(1 / 12 + 2 / 11)


def test_single_lane_baselines_keep_lane_order():
    dense = [(3, 0.7), (1, 0.6)]
    lexical = [LexicalHit(5, 2.0, 0, 0, 0)]

    assert fuse_one(dense, lexical, {"method": "dense_only"}, 60)[0] == [3, 1]
    assert fuse_one(dense, lexical, {"method": "lexical_only"}, 60)[0] == [5]


def _vectors(n=200, d=32, seed=0):
    matrix = np.random.default_rng(seed).normal(size=(n, d)).astype(np.float32)
    return matrix / np.linalg.norm(matrix, axis=1, keepdims=True)


def test_quantized_indexes_with_rescoring_find_the_exact_neighbour():
    vectors = _vectors()
    tenants = ["default"] * len(vectors)
    for quantization in ("float32", "int8", "binary"):
        index = DenseIndex(vectors, tenants, quantization=quantization, rescore_oversample=4)
        assert index.search(vectors[5], 5)[0][0] == 5
    assert DenseIndex(vectors, tenants, quantization="binary").memory_bytes == 200 * 4


def test_dense_index_filters_tenants():
    vectors = _vectors(n=10)
    tenants = ["a"] * 5 + ["b"] * 5

    hits = DenseIndex(vectors, tenants).search(vectors[0], 10, tenant_id="b")

    assert {i for i, _ in hits} == set(range(5, 10))


def test_projection_shapes_and_unit_norms():
    vectors = _vectors()

    pca = Projection("pca:8", vectors).apply(vectors)
    truncated = Projection("truncate:16", vectors).apply(vectors)

    assert pca.shape == (200, 8) and truncated.shape == (200, 16)
    assert np.allclose(np.linalg.norm(pca, axis=1), 1.0, atol=1e-5)
    with pytest.raises(ValueError):
        Projection("pca:64", vectors)


def test_plan_counts_and_skips_unsupported_combinations():
    cfg = {
        "embedding": [{"model": "m"}],
        "quantization": [{"type": "float32"}, {"type": "int8"}],
        "dense_backend": [{"type": "exact"}, {"type": "chroma"}],
        "fusion": [{"method": "weighted_rrf"}, {"method": "dbsf"}],
        "rerank": [{"model": "r", "depths": [20, 50]}],
    }

    out = plan(cfg)

    assert out["dense_indexes"] == 3  # chroma + int8 is skipped
    assert out["evaluations"] == 3 * 1 * 2 * 3
