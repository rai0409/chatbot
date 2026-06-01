from __future__ import annotations

import pytest

from rag_core import approved_similar, embedding_provider


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


def test_unknown_provider_name_fails_clearly():
    with pytest.raises(ValueError, match="Unknown embedding provider 'bogus'"):
        embedding_provider.get_embedding_provider("bogus")


def test_reserved_provider_name_fails_as_not_implemented():
    with pytest.raises(NotImplementedError, match="reserved but not implemented"):
        embedding_provider.get_embedding_provider("bge_m3")


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
