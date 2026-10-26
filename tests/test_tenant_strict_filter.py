from __future__ import annotations

import config
from rag_core import retrieval
from scripts import backfill_tenant_id


def _flatten(where):
    if not where:
        return {}
    if "$and" in where:
        flat = {}
        for clause in where["$and"]:
            flat.update(clause)
        return flat
    return dict(where)


def test_strict_filter_pushes_default_tenant_into_db_where(monkeypatch):
    monkeypatch.setattr(config, "IGNORE_SEARCHABLE", False)
    monkeypatch.setattr(config, "TENANT_FILTER_STRICT", True)

    flat = retrieval._build_base_where(tenant_id="default")

    assert flat == {"searchable": 1, "tenant_id": "default"}
    assert _flatten(retrieval._to_chroma_where(flat))["tenant_id"] == "default"


def test_strict_filter_off_keeps_legacy_default_behaviour(monkeypatch):
    monkeypatch.setattr(config, "IGNORE_SEARCHABLE", False)
    monkeypatch.setattr(config, "TENANT_FILTER_STRICT", False)

    assert "tenant_id" not in retrieval._build_base_where(tenant_id="default")


class _FakeCollection:
    name = "fake"

    def __init__(self, metadatas):
        self.ids = [f"id-{i}" for i in range(len(metadatas))]
        self.metadatas = [dict(m) for m in metadatas]
        self.updates = []

    def count(self):
        return len(self.ids)

    def get(self, include=None, limit=None, offset=0):
        end = offset + limit
        return {"ids": self.ids[offset:end], "metadatas": self.metadatas[offset:end]}

    def update(self, ids, metadatas):
        self.updates.append((list(ids), [dict(m) for m in metadatas]))
        for item_id, meta in zip(ids, metadatas):
            self.metadatas[self.ids.index(item_id)] = dict(meta)


def _collection():
    return _FakeCollection(
        [
            {"source_doc": "a.pdf"},
            {"source_doc": "b.pdf", "tenant_id": "tenant_b"},
            {"source_doc": "c.pdf", "tenant_id": ""},
        ]
    )


def test_backfill_dry_run_reports_without_writing():
    collection = _collection()

    summary = backfill_tenant_id.backfill_collection(collection, batch_size=2)

    assert summary["records"] == 3
    assert summary["missing_tenant_id"] == 2
    assert summary["updated"] == 0
    assert collection.updates == []


def test_backfill_apply_sets_default_and_keeps_other_metadata():
    collection = _collection()

    summary = backfill_tenant_id.backfill_collection(collection, apply=True, batch_size=2)

    assert summary["updated"] == 2
    assert collection.metadatas[0] == {"source_doc": "a.pdf", "tenant_id": "default"}
    assert collection.metadatas[1] == {"source_doc": "b.pdf", "tenant_id": "tenant_b"}
    assert collection.metadatas[2] == {"source_doc": "c.pdf", "tenant_id": "default"}
