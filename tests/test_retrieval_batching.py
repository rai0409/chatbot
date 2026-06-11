from __future__ import annotations

import pytest

import config
from rag_core import qa, retrieval, store


class FakeQueryCollection:
    def __init__(self):
        self.queries = []

    def query(self, query_embeddings, n_results, where=None):
        self.queries.append(list(query_embeddings))
        return {
            "documents": [["パスワード変更の手順"]],
            "metadatas": [[{"id": "c1", "source_doc": "doc.pdf", "chunk_role": "child"}]],
            "distances": [[0.2]],
        }


def _reset_keyword_index(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CHUNKS_JSONL_PATH", str(tmp_path / "missing.jsonl"))
    retrieval._INDEX_CACHE.update({"path": None, "mtime": None, "index": None})
    retrieval._KEYWORD_INDEX_WARNED_KEYS.clear()


def test_identical_queries_run_single_hybrid_pass(monkeypatch):
    calls = []

    def fake_hybrid(question, client, **kwargs):
        calls.append(question)
        return []

    monkeypatch.setattr(qa, "hybrid_retrieve", fake_hybrid)

    before, ranked, context = qa._retrieve_and_rerank(
        "暗証番号の変更",
        "暗証番号の変更",
        scoring_query="暗証番号の変更",
        client=None,
        top_k=3,
        intent="other",
        query_type="other",
    )

    assert calls == ["暗証番号の変更"]
    assert before == [] and ranked == [] and context == []


def test_differing_queries_embed_both_in_one_call(monkeypatch, tmp_path):
    _reset_keyword_index(monkeypatch, tmp_path)
    embed_calls = []

    def fake_embed(texts, client=None, **kwargs):
        embed_calls.append(list(texts))
        return [[0.1, 0.2, 0.3] for _ in texts]

    fake_collection = FakeQueryCollection()
    monkeypatch.setattr(retrieval.embedder, "embed_queries", fake_embed)
    monkeypatch.setattr(
        retrieval.store, "get_vectorstore", lambda *args, **kwargs: fake_collection
    )

    qa._retrieve_and_rerank(
        "暗証番号の変更",
        "暗証番号の変更 手順 方法",
        scoring_query="暗証番号の変更",
        client=None,
        top_k=3,
        intent="change",
        query_type="other",
    )

    assert embed_calls == [["暗証番号の変更", "暗証番号の変更 手順 方法"]]
    assert len(fake_collection.queries) == 2


def test_precomputed_query_embedding_skips_embedding(monkeypatch):
    def forbidden_embed(*args, **kwargs):
        raise AssertionError("embed_queries must not be called")

    fake_collection = FakeQueryCollection()
    monkeypatch.setattr(retrieval.embedder, "embed_queries", forbidden_embed)
    monkeypatch.setattr(
        retrieval.store, "get_vectorstore", lambda *args, **kwargs: fake_collection
    )

    hits = retrieval.vector_retrieve(
        "暗証番号の変更", None, top_k=3, query_embedding=[0.4, 0.5, 0.6]
    )

    assert len(hits) == 1
    assert fake_collection.queries == [[[0.4, 0.5, 0.6]]]


def test_callable_query_embedding_is_resolved_lazily(monkeypatch):
    def forbidden_embed(*args, **kwargs):
        raise AssertionError("embed_queries must not be called")

    fake_collection = FakeQueryCollection()
    monkeypatch.setattr(retrieval.embedder, "embed_queries", forbidden_embed)
    monkeypatch.setattr(
        retrieval.store, "get_vectorstore", lambda *args, **kwargs: fake_collection
    )

    hits = retrieval.vector_retrieve(
        "暗証番号の変更", None, top_k=3, query_embedding=lambda: [0.7, 0.8, 0.9]
    )

    assert len(hits) == 1
    assert fake_collection.queries == [[[0.7, 0.8, 0.9]]]


def test_query_embedding_batch_embeds_all_queries_once(monkeypatch):
    embed_calls = []

    def fake_embed(texts, client=None, **kwargs):
        embed_calls.append(list(texts))
        return [[0.1, float(i)] for i, _ in enumerate(texts)]

    monkeypatch.setattr(retrieval.embedder, "embed_queries", fake_embed)
    batch = retrieval.QueryEmbeddingBatch(["質問A", "質問B"])

    first = batch.get("質問A")
    second = batch.get("質問B")

    assert embed_calls == [["質問A", "質問B"]]
    assert first == [0.1, 0.0]
    assert second == [0.1, 1.0]


def test_get_vectorstore_reuses_client_and_reset_clears(monkeypatch):
    created = []

    class FakeCollection:
        name = "fake"
        metadata = {}

    class FakeClient:
        def __init__(self, path=None):
            created.append(path)

        def get_or_create_collection(self, name, metadata=None):
            return FakeCollection()

    monkeypatch.setattr(store.chromadb, "PersistentClient", FakeClient)
    store.reset_vectorstore_clients()
    try:
        store.get_vectorstore(verify_embedding_fingerprint=False)
        store.get_vectorstore(verify_embedding_fingerprint=False)
        assert len(created) == 1

        store.reset_vectorstore_clients()
        store.get_vectorstore(verify_embedding_fingerprint=False)
        assert len(created) == 2
    finally:
        store.reset_vectorstore_clients()
