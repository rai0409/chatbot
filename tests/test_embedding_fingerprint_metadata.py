from __future__ import annotations

import json

from rag_core import embedding_fingerprint as ef


class FakeCollection:
    name = "fake"

    def __init__(self):
        self.metadata = {"hnsw:space": "cosine", "note": "keep"}
        self.modified = None

    def get(self, include=None, limit=None):
        return {"embeddings": [[0.1, 0.2, 0.3]]}

    def modify(self, metadata=None):
        self.modified = dict(metadata or {})
        self.metadata.update(self.modified)


def test_stamp_collection_metadata_includes_source_hash_and_preserves_mutable_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(
        ef.embedding_provider,
        "active_fingerprint",
        lambda provider_name=None: {"embed_provider": "local", "embed_model": "m"},
    )
    source = tmp_path / "chunks.jsonl"
    source.write_text(json.dumps({"id": "c1", "text": "x"}) + "\n", encoding="utf-8")
    collection = FakeCollection()

    stamp = ef.stamp_collection_metadata(collection, source_jsonl_path=source, chunk_count=1)

    assert stamp["embed_provider"] == "local"
    assert stamp["embed_model"] == "m"
    assert stamp["embedding_dim"] == 3
    assert stamp["chunk_count"] == 1
    assert stamp["source_jsonl_sha256"] == ef.source_jsonl_sha256(source)
    assert collection.modified["note"] == "keep"
    assert "hnsw:space" not in collection.modified


def test_fingerprint_status_reports_core_and_extended(monkeypatch):
    monkeypatch.setattr(
        ef.embedding_provider,
        "active_fingerprint",
        lambda provider_name=None: {"embed_provider": "local", "embed_model": "m"},
    )

    status = ef.fingerprint_status({"embed_provider": "local", "embed_model": "m", "chunk_count": 2})

    assert status["present"] is True
    assert status["matches_active"] is True
    assert status["extended"]["chunk_count"] == 2
    assert "source_jsonl_sha256" in status["missing_extended_keys"]
