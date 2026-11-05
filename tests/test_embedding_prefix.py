from __future__ import annotations

import pytest

from rag_core import embedding_fingerprint, embedding_provider, store


class _RecordingModel:
    def __init__(self):
        self.calls = []

    def encode(self, texts, normalize_embeddings):
        self.calls.append(list(texts))
        rows = [[0.0] for _ in texts]

        class Encoded:
            def tolist(self):
                return rows

        return Encoded()


class _FakeCollection:
    def __init__(self, metadata):
        self.name = "fake"
        self.metadata = dict(metadata)


@pytest.fixture
def ruri_model(monkeypatch):
    monkeypatch.setenv("EMBED_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_EMBED_MODEL", "cl-nagoya/ruri-v3-130m")
    monkeypatch.delenv("LOCAL_EMBED_MODEL_PATH", raising=False)
    monkeypatch.delenv("EMBED_QUERY_PREFIX", raising=False)
    monkeypatch.delenv("EMBED_DOCUMENT_PREFIX", raising=False)
    model = _RecordingModel()
    monkeypatch.setattr(embedding_provider, "_get_local_model", lambda model_name: model)
    return model


def test_ruri_v3_queries_and_documents_get_model_card_prefixes(ruri_model):
    embedding_provider.embed_queries(["退職の手続き"])
    embedding_provider.embed_documents(["退職時は保険証を返却します"])

    assert ruri_model.calls == [
        ["検索クエリ: 退職の手続き"],
        ["検索文書: 退職時は保険証を返却します"],
    ]
    assert embedding_provider.active_prefix_scheme() == "ruri-v3-retrieval"


def test_non_ruri_models_keep_texts_unchanged(monkeypatch):
    monkeypatch.delenv("EMBED_QUERY_PREFIX", raising=False)
    monkeypatch.delenv("EMBED_DOCUMENT_PREFIX", raising=False)
    monkeypatch.delenv("LOCAL_EMBED_MODEL_PATH", raising=False)
    model = _RecordingModel()
    monkeypatch.setattr(embedding_provider, "_get_local_model", lambda model_name: model)
    provider = embedding_provider.LocalEmbeddingProvider("fake-local-model")

    provider.embed_queries(["q"])

    assert model.calls == [["q"]]
    assert provider.prefix_scheme == "none"


def test_explicit_empty_prefix_disables_model_default(ruri_model, monkeypatch):
    monkeypatch.setenv("EMBED_QUERY_PREFIX", "")
    monkeypatch.setenv("EMBED_DOCUMENT_PREFIX", "")

    embedding_provider.embed_queries(["q"])

    assert ruri_model.calls == [["q"]]
    assert embedding_provider.active_prefix_scheme() == "none"


def test_custom_prefixes_get_a_stable_custom_scheme():
    first = embedding_provider.prefix_scheme_name("query: ", "passage: ")

    assert first.startswith("custom-")
    assert first == embedding_provider.prefix_scheme_name("query: ", "passage: ")
    assert first != embedding_provider.prefix_scheme_name("query: ", "")


def test_reranker_model_names_do_not_get_embedding_prefixes():
    assert embedding_provider._model_default_prefixes("cl-nagoya/ruri-v3-reranker-310m") == ("", "")


def test_active_fingerprint_stays_two_keys(ruri_model):
    assert embedding_provider.active_fingerprint() == {
        "embed_provider": "local",
        "embed_model": "cl-nagoya/ruri-v3-130m",
    }


def test_prefix_scheme_mismatch_is_rejected(ruri_model):
    collection = _FakeCollection(
        {
            "embed_provider": "local",
            "embed_model": "cl-nagoya/ruri-v3-130m",
            "embed_prefix_scheme": "none",
        }
    )

    with pytest.raises(RuntimeError, match="prefix scheme mismatch"):
        store.verify_collection_fingerprint(collection)


def test_matching_scheme_and_pre_prefix_stamps_pass(ruri_model):
    store.verify_collection_fingerprint(
        _FakeCollection(
            {
                "embed_provider": "local",
                "embed_model": "cl-nagoya/ruri-v3-130m",
                "embed_prefix_scheme": "ruri-v3-retrieval",
            }
        )
    )
    store.verify_collection_fingerprint(
        _FakeCollection({"embed_provider": "local", "embed_model": "cl-nagoya/ruri-v3-130m"})
    )


def test_extended_stamp_records_prefix_scheme(ruri_model):
    stamp = embedding_fingerprint.build_collection_fingerprint_metadata(
        chunk_count=1, created_at="2026-01-01T00:00:00+00:00"
    )

    assert stamp["embed_prefix_scheme"] == "ruri-v3-retrieval"
    status = embedding_fingerprint.fingerprint_status(stamp)
    assert status["matches_active"] is True
    assert status["prefix_scheme_matches"] is True


def test_default_embedding_model_is_ruri_130m():
    assert (
        embedding_provider.DEFAULT_LOCAL_EMBED_MODEL
        == "cl-nagoya/ruri-v3-130m"
    )
