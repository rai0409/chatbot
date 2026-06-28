from __future__ import annotations

import pytest

from rag_core import approved_similar, embedding_provider


class _FakeEmbeddingItem:
    def __init__(self, embedding):
        self.embedding = embedding


class _FakeEmbeddingResponse:
    def __init__(self, embeddings):
        self.data = [_FakeEmbeddingItem(embedding) for embedding in embeddings]


class _FakeOpenAIClient:
    def __init__(self, embeddings):
        self.calls = []
        self._embeddings = embeddings
        self.embeddings = self

    def create(self, *, model, input):
        self.calls.append({"model": model, "input": input})
        return _FakeEmbeddingResponse(self._embeddings)


def test_default_provider_resolves_to_local(monkeypatch):
    monkeypatch.delenv("EMBED_PROVIDER", raising=False)

    provider = embedding_provider.get_embedding_provider()

    assert provider.name == "local"


def test_embed_queries_delegates_to_local_provider(monkeypatch):
    class FakeModel:
        def encode(self, texts, normalize_embeddings):
            assert texts == ["a", "b"]
            assert normalize_embeddings is True

            class Encoded:
                def tolist(self):
                    return [[0.1, 0.2], [0.3, 0.4]]

            return Encoded()

    monkeypatch.setenv("EMBED_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_EMBED_MODEL", "fake-local-model")
    monkeypatch.setattr(
        embedding_provider,
        "_get_local_model",
        lambda model_name: FakeModel() if model_name == "fake-local-model" else None,
    )

    assert embedding_provider.embed_queries(["a", "b"]) == [[0.1, 0.2], [0.3, 0.4]]


def test_bge_m3_provider_resolves_by_name():
    provider = embedding_provider.get_embedding_provider("bge_m3")

    assert provider.name == "bge_m3"
    assert provider.model_name == "BAAI/bge-m3"


def test_bge_m3_provider_delegates_to_expected_model(monkeypatch):
    class FakeModel:
        def encode(self, texts, normalize_embeddings):
            assert texts == ["自由回答", "個人情報"]
            assert normalize_embeddings is True

            class Encoded:
                def tolist(self):
                    return [[0.11, 0.22], [0.33, 0.44]]

            return Encoded()

    monkeypatch.setattr(
        embedding_provider,
        "_get_bge_m3_model",
        lambda model_name="BAAI/bge-m3": FakeModel()
        if model_name == "BAAI/bge-m3"
        else None,
    )

    provider = embedding_provider.get_embedding_provider("bge_m3")

    assert provider.embed_queries(["自由回答", "個人情報"]) == [[0.11, 0.22], [0.33, 0.44]]


def test_bge_m3_missing_dependency_error_is_clear(monkeypatch):
    embedding_provider._get_sentence_transformer_model.cache_clear()

    def missing_sentence_transformer(provider_name):
        raise RuntimeError(
            f"{provider_name} embeddings require sentence-transformers. "
            f"Install it and set EMBED_PROVIDER={provider_name}."
        )

    monkeypatch.setattr(
        embedding_provider,
        "_load_sentence_transformer_class",
        missing_sentence_transformer,
    )

    with pytest.raises(RuntimeError, match="bge_m3 embeddings require sentence-transformers"):
        embedding_provider.get_embedding_provider("bge_m3").embed_queries(["q"])


def test_openai_provider_uses_explicit_client(monkeypatch):
    client = _FakeOpenAIClient([[0.1, 0.2], [0.3, 0.4]])
    monkeypatch.setenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
    monkeypatch.setattr(
        embedding_provider,
        "_create_openai_client",
        lambda: pytest.fail("explicit client should be used"),
    )

    embeddings = embedding_provider.get_embedding_provider("openai").embed_queries(
        ["a", "b"],
        client=client,
    )

    assert embeddings == [[0.1, 0.2], [0.3, 0.4]]
    assert client.calls == [{"model": "text-embedding-3-small", "input": ["a", "b"]}]


def test_openai_provider_creates_client_from_config_when_missing(monkeypatch):
    created = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            created["kwargs"] = kwargs
            self._client = _FakeOpenAIClient([[0.5, 0.6]])
            self.embeddings = self._client.embeddings

    monkeypatch.setenv("OPENAI_API_KEY", "OPENAI_KEY_TEST_PLACEHOLDER")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
    monkeypatch.setattr(embedding_provider, "_load_openai_class", lambda: FakeOpenAI)

    embeddings = embedding_provider.get_embedding_provider("openai").embed_queries(["hello"])

    assert embeddings == [[0.5, 0.6]]
    assert created["kwargs"] == {
        "api_key": "OPENAI_KEY_TEST_PLACEHOLDER",
        "base_url": "https://example.test/v1",
    }


def test_openai_provider_missing_api_key_error_is_clear(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(embedding_provider.config, "OPENAI_API_KEY", "")
    monkeypatch.setattr(
        embedding_provider,
        "_load_openai_class",
        lambda: pytest.fail("OpenAI SDK should not be loaded without an API key"),
    )

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY is missing for OpenAI embeddings"):
        embedding_provider.get_embedding_provider("openai").embed_queries(["hello"])


def test_unknown_provider_name_fails_clearly():
    with pytest.raises(ValueError, match="Unknown embedding provider 'bogus'"):
        embedding_provider.get_embedding_provider("bogus")


def test_reserved_provider_name_fails_as_not_implemented():
    with pytest.raises(NotImplementedError, match="reserved but not implemented"):
        embedding_provider.get_embedding_provider("qwen")


def test_approved_similar_candidate_uses_embedding_provider(monkeypatch):
    class FakeCollection:
        def query(self, **kwargs):
            assert kwargs["query_embeddings"] == [[0.0, 1.0]]
            return {
                "documents": [["Q: 15問程度の項目はフリーアンサーも含まれる。\nA: フリーアンサーも含みます。"]],
                "metadatas": [
                    [
                        {
                            "qa_id": "qa_free_answer",
                            "question_text": "15問程度の項目はフリーアンサーも含まれるという認識で良いでしょうか。",
                            "answer_text": "フリーアンサーも含みます。",
                            "normalized_question": "15問程度の項目はフリーアンサーも含まれるという認識で良いでしょうか。",
                            "chunk_type": "qa_pair",
                            "doc_type": "approved_qa_pair",
                            "searchable": 1,
                        }
                    ]
                ],
                "distances": [[0.1]],
            }

    monkeypatch.setattr(approved_similar.store, "get_vectorstore", lambda **kwargs: FakeCollection())
    monkeypatch.setattr(
        embedding_provider,
        "embed_queries",
        lambda queries, client=None, provider_name=None: [[0.0, 1.0]],
    )

    candidates = approved_similar.search_approved_similar_candidates(
        "15問に自由回答は入りますか？",
        top_k=1,
    )

    assert candidates[0]["qa_id"] == "qa_free_answer"
