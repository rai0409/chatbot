import pytest

qdrant_client = pytest.importorskip("qdrant_client")

from rag_core.vector_backend import QdrantCollection


_COUNTER = iter(range(1_000_000))


def _collection(name="contract"):
    # A unique name per call: QdrantCollection caches "exists" per (client id, name), and
    # ids of garbage-collected in-memory clients are reused by later tests.
    return QdrantCollection(f"{name}_{next(_COUNTER)}", client=qdrant_client.QdrantClient(":memory:"))


def _seed(col):
    col.upsert(
        ids=["a1", "a2", "b1"],
        documents=["doc a1", "doc a2", "doc b1"],
        embeddings=[[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]],
        metadatas=[{"tenant_id": "ta"}, {"tenant_id": "ta"}, {"tenant_id": "tb"}],
    )
    return col


def test_get_applies_ids_and_where_together():
    col = _seed(_collection())
    res = col.get(ids=["a1", "b1"], where={"tenant_id": "ta"}, include=["metadatas"])
    assert sorted(res["ids"]) == ["a1"]


def test_get_with_empty_ids_returns_nothing():
    col = _seed(_collection())
    assert col.get(ids=[], include=["metadatas"])["ids"] == []


def test_delete_applies_ids_and_where_together():
    col = _seed(_collection())
    col.delete(ids=["a1", "b1"], where={"tenant_id": "ta"})
    remaining = sorted(col.get(include=["metadatas"])["ids"])
    assert remaining == ["a2", "b1"]


def test_delete_with_empty_ids_deletes_nothing():
    col = _seed(_collection())
    col.delete(ids=[], where={"tenant_id": "ta"})
    assert col.count() == 3


def test_get_offset_pages_cover_every_row_once():
    col = _collection()
    col.upsert(
        ids=[f"id{i}" for i in range(5)],
        documents=[f"doc {i}" for i in range(5)],
        embeddings=[[1.0, float(i)] for i in range(5)],
        metadatas=[{"tenant_id": "ta"} for _ in range(5)],
    )
    seen = []
    for start in (0, 2, 4):
        seen.extend(col.get(limit=2, offset=start, include=["metadatas"])["ids"])
    assert sorted(seen) == [f"id{i}" for i in range(5)]


@pytest.mark.parametrize(
    "ids, docs, vecs, metas",
    [
        (["x1", "x2"], ["d1"], [[1.0, 0.0], [0.0, 1.0]], [{}, {}]),
        (["x1", "x2"], ["d1", "d2"], [[1.0, 0.0], [1.0]], [{}, {}]),
        (["x1"], ["d1"], [[float("nan"), 0.0]], [{}]),
        (["x1", "x1"], ["d1", "d2"], [[1.0, 0.0], [0.0, 1.0]], [{}, {}]),
    ],
)
def test_invalid_upsert_writes_nothing(ids, docs, vecs, metas):
    col = _collection()
    with pytest.raises(ValueError):
        col.upsert(ids=ids, documents=docs, embeddings=vecs, metadatas=metas)
    assert col.count() == 0


def test_ingest_guard_rejects_cross_tenant_id_collision():
    from scripts.ingest_canonical_jsonl import _assert_no_cross_tenant_id_collision

    col = _seed(_collection())
    with pytest.raises(ValueError):
        _assert_no_cross_tenant_id_collision(col, ["a1"], [{"tenant_id": "tb"}])
    _assert_no_cross_tenant_id_collision(col, ["a1"], [{"tenant_id": "ta"}])
    _assert_no_cross_tenant_id_collision(col, ["new"], [{"tenant_id": "tb"}])


def test_update_merges_metadata_and_keeps_text_and_vector():
    col = _seed(_collection())
    col.update(ids=["a1", "missing"], metadatas=[{"tenant_id": "ta", "note": "x"}, {"tenant_id": "zz"}])
    res = col.get(ids=["a1"], include=["documents", "metadatas", "embeddings"])
    assert res["documents"] == ["doc a1"]
    assert res["metadatas"][0] == {"tenant_id": "ta", "note": "x"}
    assert list(res["embeddings"][0]) == pytest.approx([1.0, 0.0])
    assert col.count() == 3
    assert col.get(ids=["missing"], include=["metadatas"])["ids"] == []


def test_update_refuses_text_or_vector_changes():
    col = _seed(_collection())
    with pytest.raises(NotImplementedError):
        col.update(ids=["a1"], metadatas=[{"tenant_id": "ta"}], documents=["changed"])
    with pytest.raises(NotImplementedError):
        col.update(ids=["a1"], metadatas=[{"tenant_id": "ta"}], embeddings=[[0.0, 1.0]])


def test_backfill_tenant_id_works_on_qdrant_with_paging():
    from scripts.backfill_tenant_id import backfill_collection

    col = _collection()
    col.upsert(
        ids=[f"r{i}" for i in range(5)],
        documents=[f"doc {i}" for i in range(5)],
        embeddings=[[1.0, float(i)] for i in range(5)],
        metadatas=[{} if i % 2 else {"tenant_id": "ta"} for i in range(5)],
    )
    dry = backfill_collection(col, apply=False, batch_size=2)
    assert dry["missing_tenant_id"] == 2 and dry["updated"] == 0
    done = backfill_collection(col, apply=True, batch_size=2)
    assert done["updated"] == 2
    res = col.get(include=["metadatas"])
    tenants = dict(zip(res["ids"], [m["tenant_id"] for m in res["metadatas"]]))
    assert tenants == {"r0": "ta", "r1": "default", "r2": "ta", "r3": "default", "r4": "ta"}


@pytest.fixture
def qdrant_ingest(monkeypatch, tmp_path):
    import config
    from scripts import ingest_canonical_jsonl as ingest

    monkeypatch.setattr(config, "VECTORSTORE_DIR", str(tmp_path))
    col = _collection("ingest")
    monkeypatch.setattr(ingest.store, "get_vectorstore", lambda **kwargs: col)
    monkeypatch.setattr(ingest.store, "stamp_collection_fingerprint", lambda c: {})
    monkeypatch.setattr(ingest.embedder, "is_local_provider", lambda: True)
    monkeypatch.setattr(ingest.embedder, "embed_documents", lambda texts, client=None: [[1.0, 0.0] for _ in texts])
    return ingest, col


def _row(i, source="doc.pdf", tenant="default"):
    return {"id": i, "text": f"text {i}", "source_doc": source, "tenant_id": tenant}


def test_ingest_refuses_an_id_owned_by_another_tenant(qdrant_ingest):
    ingest, col = qdrant_ingest
    ingest.ingest_canonical_rows([_row("x1", tenant="ta")])
    with pytest.raises(ValueError):
        ingest.ingest_canonical_rows([_row("x1", tenant="tb")])
    res = col.get(ids=["x1"], include=["metadatas"])
    assert [m["tenant_id"] for m in res["metadatas"]] == ["ta"]


def test_reingest_removes_only_stale_rows_of_the_same_tenant_and_document(qdrant_ingest):
    ingest, col = qdrant_ingest
    ingest.ingest_canonical_rows([_row("ta:a1", tenant="ta"), _row("ta:a2", tenant="ta"), _row("tb:a1", tenant="tb")])
    result = ingest.ingest_canonical_rows([_row("ta:a1", tenant="ta")])
    assert sorted(col.get(include=[])["ids"]) == ["ta:a1", "tb:a1"]
    assert result["stale_deleted"] == 1


def test_delete_documents_passes_tenant_and_document_to_the_final_delete(monkeypatch, tmp_path):
    import contextlib

    from scripts import ingest_canonical_jsonl as ingest
    from webapi import collection_admin

    col = _collection("served")
    col.upsert(
        ids=["a1", "a2", "b1"],
        documents=["d", "d", "d"],
        embeddings=[[1.0, 0.0]] * 3,
        metadatas=[
            {"tenant_id": "ta", "source_doc": "d1.pdf"},
            {"tenant_id": "ta", "source_doc": "d1.pdf"},
            {"tenant_id": "tb", "source_doc": "d1.pdf"},
        ],
    )
    calls = []

    class _Spy:
        def get(self, **kwargs):
            return col.get(**kwargs)

        def delete(self, **kwargs):
            calls.append(kwargs)
            return col.delete(**kwargs)

    monkeypatch.setattr(collection_admin, "_open", lambda collection: _Spy())
    monkeypatch.setattr(collection_admin, "_keyword_path", lambda collection: str(tmp_path / "kw.jsonl"))
    monkeypatch.setattr(collection_admin.ingestion_jobs, "_collection_write_lock", lambda name: contextlib.nullcontext())
    monkeypatch.setattr(ingest, "remove_keyword_documents", lambda path, keys: {"removed": 0})
    report = collection_admin.delete_documents(None, tenant="ta", source_docs=["d1.pdf"], execute=True)
    assert report["chunks"] == 2
    assert calls and all(c["where"] == {"$and": [{"tenant_id": "ta"}, {"source_doc": "d1.pdf"}]} for c in calls)
    assert sorted(col.get(include=[])["ids"]) == ["b1"]
