from __future__ import annotations

import json
import logging
import sys

import pytest

from rag_core import embedding_provider, store
from scripts import ingest_canonical_jsonl
from webapi import main


class FakeCollection:
    def __init__(self, metadata=None, name="fake_collection"):
        self.name = name
        self.metadata = dict(metadata or {})
        self.upserts = []

    def upsert(self, ids, documents, embeddings, metadatas):
        self.upserts.append({"ids": list(ids), "documents": list(documents)})

    def modify(self, metadata=None, name=None):
        # Mirror chromadb: modify() rejects immutable hnsw:* keys, and the
        # collection preserves its existing configuration (e.g. hnsw:space)
        # across metadata updates rather than wiping it. Merge, don't replace,
        # so stamping the embedding fingerprint never clobbers creation
        # metadata.
        if metadata is not None:
            if any(str(key).startswith("hnsw:") for key in metadata):
                raise ValueError(
                    "Changing the distance function of a collection once it is "
                    "created is not supported currently."
                )
            self.metadata.update(metadata)
        if name is not None:
            self.name = name


def _use_local_test_model(monkeypatch):
    monkeypatch.setenv("EMBED_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_EMBED_MODEL", "test-local-model")


def _missing_fingerprint_warnings(caplog):
    return [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING
        and "embedding_fingerprint_missing" in record.getMessage()
    ]


def test_ingest_stamps_collection_fingerprint(monkeypatch, tmp_path):
    _use_local_test_model(monkeypatch)
    jsonl = tmp_path / "canonical.jsonl"
    rows = [
        {"id": "c1", "text": "パスワードの再設定手順", "source_pages": [1]},
        {"id": "c2", "text": "問い合わせ窓口の案内", "source_pages": [2]},
    ]
    jsonl.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    fake = FakeCollection(metadata={"hnsw:space": "cosine"})
    monkeypatch.setattr(
        ingest_canonical_jsonl.store,
        "get_vectorstore",
        lambda collection_name=None, **kwargs: fake,
    )
    monkeypatch.setattr(
        ingest_canonical_jsonl.embedder,
        "embed_documents",
        lambda texts, client=None: [[0.1, 0.2, 0.3] for _ in texts],
    )
    monkeypatch.setattr(
        ingest_canonical_jsonl.embedder,
        "embed_queries",
        lambda *args, **kwargs: pytest.fail("ingestion must use embed_documents"),
    )
    monkeypatch.setattr(sys, "argv", ["ingest_canonical_jsonl.py", str(jsonl)])

    assert ingest_canonical_jsonl.main() == 0

    assert fake.metadata["embed_provider"] == "local"
    assert fake.metadata["embed_model"] == "test-local-model"
    assert fake.metadata["hnsw:space"] == "cosine"
    assert fake.upserts and fake.upserts[0]["ids"] == ["c1", "c2"]


def test_query_time_mismatch_raises_with_both_fingerprints(monkeypatch):
    _use_local_test_model(monkeypatch)
    fake = FakeCollection(
        metadata={
            "hnsw:space": "cosine",
            "embed_provider": "openai",
            "embed_model": "text-embedding-3-small",
        }
    )

    with pytest.raises(RuntimeError) as excinfo:
        store.verify_collection_fingerprint(fake)

    message = str(excinfo.value)
    assert "openai" in message
    assert "text-embedding-3-small" in message
    assert "local" in message
    assert "test-local-model" in message


def test_matching_fingerprint_passes(monkeypatch):
    _use_local_test_model(monkeypatch)
    fake = FakeCollection(
        metadata={"embed_provider": "local", "embed_model": "test-local-model"}
    )

    store.verify_collection_fingerprint(fake)


def test_legacy_unstamped_collection_warns_once_and_does_not_fail(monkeypatch, caplog):
    _use_local_test_model(monkeypatch)
    store._UNSTAMPED_WARNED.clear()
    fake = FakeCollection(metadata={"hnsw:space": "cosine"}, name="legacy_collection")

    with caplog.at_level(logging.WARNING, logger="rag_core.store"):
        store.verify_collection_fingerprint(fake)
        store.verify_collection_fingerprint(fake)

    warnings = _missing_fingerprint_warnings(caplog)
    assert len(warnings) == 1
    payload = json.loads(warnings[0].getMessage().split("embedding_fingerprint_missing ", 1)[1])
    assert payload["collection"] == "legacy_collection"
    assert payload["active"] == {
        "embed_provider": "local",
        "embed_model": "test-local-model",
    }


def test_health_includes_embedding_fingerprint(monkeypatch):
    _use_local_test_model(monkeypatch)

    payload = main.health()

    assert payload["status"] == "ok"
    assert payload["embed_provider"] == "local"
    assert payload["embed_model"] == "test-local-model"


def test_embedding_client_uses_unified_default(monkeypatch):
    monkeypatch.delenv("EMBED_PROVIDER", raising=False)
    assert embedding_provider.default_provider_name() == "local"
    assert main._embedding_client() is None


def test_embedding_client_non_local_uses_openai_client(monkeypatch):
    monkeypatch.setenv("EMBED_PROVIDER", "openai")
    sentinel = object()
    monkeypatch.setattr(main, "ensure_openai_client", lambda base_url=None: sentinel)

    assert main._embedding_client() is sentinel
