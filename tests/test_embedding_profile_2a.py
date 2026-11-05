import json

import pytest

from rag_core import embedding_fingerprint, embedding_provider, store


class _Meta:
    def __init__(self, name="c", metadata=None):
        self.name = name
        self.metadata = dict(metadata or {})

    def modify(self, metadata=None):
        self.metadata.update(dict(metadata or {}))


@pytest.fixture(autouse=True)
def _local_provider(monkeypatch):
    monkeypatch.setenv("EMBED_PROVIDER", "local")
    monkeypatch.delenv("EMBED_FINGERPRINT_STRICT", raising=False)


def _stamped(**extra):
    meta = dict(embedding_provider.active_fingerprint())
    meta["embed_prefix_scheme"] = embedding_provider.active_prefix_scheme()
    meta.update(embedding_provider.active_profile())
    meta["embedding_dim"] = 4
    meta.update(extra)
    return _Meta(metadata=meta)


def test_every_provider_declares_its_profile():
    expected = {"embed_normalize": "true", "embed_distance": "cosine", "profile_version": "1"}
    for provider in (
        embedding_provider.LocalEmbeddingProvider(),
        embedding_provider.BgeM3EmbeddingProvider(),
        embedding_provider.OpenAIEmbeddingProvider(),
    ):
        assert embedding_provider.provider_profile(provider) == expected


def test_extended_stamp_and_status_carry_the_profile():
    stamp = embedding_fingerprint.build_collection_fingerprint_metadata(
        chunk_count=1, created_at="2026-01-01T00:00:00+00:00"
    )
    assert stamp["embed_normalize"] == "true" and stamp["embed_distance"] == "cosine"
    assert embedding_fingerprint.fingerprint_status(stamp)["profile_matches_active"] is True
    stamp["embed_normalize"] = "false"
    assert embedding_fingerprint.fingerprint_status(stamp)["profile_matches_active"] is False
    legacy = {k: stamp[k] for k in ("embed_provider", "embed_model")}
    assert embedding_fingerprint.fingerprint_status(legacy)["profile_matches_active"] is None


def test_complete_stamp_passes_in_strict_mode(monkeypatch):
    monkeypatch.setenv("EMBED_FINGERPRINT_STRICT", "true")
    store.verify_collection_fingerprint(_stamped())


def test_unstamped_collection_is_refused_only_in_strict_mode(monkeypatch):
    store.verify_collection_fingerprint(_Meta(name="strict_legacy_a"))
    monkeypatch.setenv("EMBED_FINGERPRINT_STRICT", "true")
    with pytest.raises(RuntimeError, match="no embedding fingerprint"):
        store.verify_collection_fingerprint(_Meta(name="strict_legacy_b"))


@pytest.mark.parametrize("key, value", [("embed_normalize", "false"), ("embed_distance", "dot")])
def test_profile_mismatch_is_always_refused(key, value):
    with pytest.raises(RuntimeError, match="profile mismatch"):
        store.verify_collection_fingerprint(_stamped(**{key: value}))


def test_incomplete_profile_is_refused_only_in_strict_mode(monkeypatch):
    incomplete = _stamped()
    del incomplete.metadata["embed_distance"]
    store.verify_collection_fingerprint(incomplete)
    monkeypatch.setenv("EMBED_FINGERPRINT_STRICT", "true")
    with pytest.raises(RuntimeError, match="incomplete embedding profile"):
        store.verify_collection_fingerprint(incomplete)


def test_stamp_collection_profile_keeps_other_metadata_and_skips_hnsw():
    col = _Meta(metadata={"hnsw:space": "cosine", "note": "keep"})
    profile = store.stamp_collection_profile(col)
    assert profile == embedding_provider.active_profile()
    assert col.metadata["note"] == "keep" and col.metadata["embed_distance"] == "cosine"
    assert col.metadata["embed_prefix_scheme"] == embedding_provider.active_prefix_scheme()


def test_embedding_dim_is_checked_and_stamped_once_known():
    store.check_and_stamp_embedding_dim(_stamped(embedding_dim=3), 3)
    with pytest.raises(ValueError, match="dimension mismatch"):
        store.check_and_stamp_embedding_dim(_stamped(embedding_dim=3), 4)
    fresh = _Meta(metadata={"hnsw:space": "cosine"})
    store.check_and_stamp_embedding_dim(fresh, 5)
    assert fresh.metadata["embedding_dim"] == 5


_COUNTER = iter(range(1_000_000))


def _qdrant_collection(name):
    qdrant_client = pytest.importorskip("qdrant_client")
    from rag_core.vector_backend import QdrantCollection

    # Unique name: "exists" is cached per (client id, name) and ids of freed in-memory clients are reused.
    return QdrantCollection(f"{name}_{next(_COUNTER)}", client=qdrant_client.QdrantClient(":memory:"))


def test_ingest_stamps_the_profile_and_refuses_another_dimension(monkeypatch, tmp_path):
    import config
    from scripts import ingest_canonical_jsonl as ingest

    monkeypatch.setattr(config, "VECTORSTORE_DIR", str(tmp_path))
    col = _qdrant_collection("dim_guard")
    dims = {"value": 2}
    monkeypatch.setattr(ingest.store, "get_vectorstore", lambda **kwargs: col)
    monkeypatch.setattr(ingest.embedder, "is_local_provider", lambda: True)
    monkeypatch.setattr(ingest.embedder, "embed_documents", lambda texts, client=None: [[1.0] * dims["value"] for _ in texts])

    def row(i):
        return {"id": i, "text": f"text {i}", "source_doc": "doc.pdf", "tenant_id": "default"}

    ingest.ingest_canonical_rows([row("r1")])
    assert col.metadata["embedding_dim"] == 2
    assert col.metadata["embed_distance"] == "cosine" and col.metadata["embed_normalize"] == "true"
    # A collection written by the ingest code carries a complete stamp, so strict mode accepts it.
    monkeypatch.setenv("EMBED_FINGERPRINT_STRICT", "true")
    store.verify_collection_fingerprint(col)
    monkeypatch.delenv("EMBED_FINGERPRINT_STRICT")
    dims["value"] = 3
    with pytest.raises(ValueError, match="dimension mismatch"):
        ingest.ingest_canonical_rows([row("r2")])
    assert sorted(col.get(include=[])["ids"]) == ["r1"]


def _consistency_setup(monkeypatch, tmp_path, keyword_ids, vector_ids):
    import config
    from scripts import check_index_consistency as cic

    monkeypatch.setattr(config, "VECTORSTORE_DIR", str(tmp_path))
    keyword_file = tmp_path / "kw.jsonl"
    keyword_file.write_text(
        "".join(json.dumps({"id": i, "index_generation": "g1"}) + "\n" for i in keyword_ids), encoding="utf-8"
    )
    col = _qdrant_collection("consistency")
    col.upsert(
        ids=list(vector_ids),
        documents=["d"] * len(vector_ids),
        embeddings=[[1.0, 0.0]] * len(vector_ids),
        metadatas=[{"tenant_id": "ta", "index_generation": "g1"}] * len(vector_ids),
    )
    monkeypatch.setattr(cic, "keyword_index_path_for_collection", lambda name: str(keyword_file))
    monkeypatch.setattr(cic.store, "get_vectorstore", lambda **kwargs: col)
    return cic


def test_full_check_also_reports_vector_rows_missing_from_the_keyword_corpus(monkeypatch, tmp_path):
    cic = _consistency_setup(monkeypatch, tmp_path, ["k1", "k2"], ["k1", "k2", "v_only"])
    assert cic.check_collection(None)["consistent"] is True
    full = cic.check_collection(None, full=True)
    assert full["vector_only"] == 1 and full["consistent"] is False
    assert full["examples"]["vector_only"] == ["v_only"]


def test_check_reports_keyword_rows_missing_from_the_vectors(monkeypatch, tmp_path):
    cic = _consistency_setup(monkeypatch, tmp_path, ["k1", "k2", "k3"], ["k1", "k2"])
    report = cic.check_collection(None)
    assert report["missing_in_vector"] == 1 and report["consistent"] is False
