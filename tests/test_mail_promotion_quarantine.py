"""Promotion quarantine tests use synthetic, in-memory records only."""
from contextlib import nullcontext

import pytest

from webapi import collection_admin


class Store:
    def __init__(self, rows):
        self.rows = rows
        self.writes = []

    def get(self, ids=None, where=None, include=None):
        rows = self.rows if ids is None else [r for r in self.rows if r[0] in ids]
        return {"ids": [r[0] for r in rows], "metadatas": [r[1] for r in rows],
                "embeddings": [[0.1] for r in rows], "documents": ["synthetic" for r in rows]}

    def upsert(self, **kwargs):
        self.writes.append(kwargs)


@pytest.fixture
def setup_promotion(monkeypatch):
    from scripts import ingest_canonical_jsonl

    source = Store([])
    target = Store([])
    monkeypatch.setattr(collection_admin.ingestion_jobs, "is_production_collection", lambda x: False)
    monkeypatch.setattr(collection_admin, "_staging_owned", lambda *a, **kw: None)
    monkeypatch.setattr(collection_admin.ingestion_jobs, "_collection_write_lock", lambda x: nullcontext())
    monkeypatch.setattr(collection_admin, "_open", lambda name: source if name else target)
    monkeypatch.setattr(ingest_canonical_jsonl, "iter_keyword_documents", lambda *a: iter([]))
    return source, target


@pytest.mark.parametrize("marker", [
    {"id": "tenant:email:abc:h1"}, {"type": "email"}, {"source_type": "EMAIL"}, {"doc_type": "email"},
    {"source_doc": "mail:synthetic"}, {"doc_id": "mail:synthetic"},
    {"id": "tenant:email:" + "0" * 24 + ":h" + "0" * 12},
])
@pytest.mark.parametrize("execute", [False, True])
@pytest.mark.parametrize("mixed", [False, True])
def test_mail_refused_before_target_access(setup_promotion, monkeypatch, marker, execute, mixed):
    source, target = setup_promotion
    meta = {"tenant_id": "tenant", "type": "pdf", "source_doc": "synthetic.pdf", **marker}
    cid = marker.get("id", "synthetic-id")
    source.rows = [(cid, meta)]
    if mixed:
        source.rows.insert(0, ("normal-id", {"tenant_id": "tenant", "type": "pdf", "source_doc": "normal.pdf"}))
    monkeypatch.setattr(collection_admin, "_open", lambda name: source if name else pytest.fail("target accessed"))
    with pytest.raises(ValueError, match="quarantined"):
        collection_admin.promote_staging("staging", tenant="tenant", execute=execute)
    assert target.writes == []


@pytest.mark.parametrize("meta", [None, {}, {"tenant_id": "tenant", "type": "pdf"},
    {"tenant_id": "tenant", "source_doc": "x.pdf"},
    {"tenant_id": "other", "type": "pdf", "source_doc": "x.pdf"},
    {"tenant_id": "tenant", "type": "pdf", "source_doc": "x.pdf", "id": "different"}])
def test_incomplete_refused(setup_promotion, meta):
    source, _ = setup_promotion
    source.rows = [("id", meta)]
    with pytest.raises(ValueError, match="promotion refused"):
        collection_admin.promote_staging("staging", tenant="tenant", execute=True)


def test_mail_in_keyword_rows_refused(setup_promotion, monkeypatch):
    from scripts import ingest_canonical_jsonl

    source, target = setup_promotion
    source.rows = [("id", {"tenant_id": "tenant", "type": "pdf", "source_doc": "x.pdf"})]
    monkeypatch.setattr(ingest_canonical_jsonl, "iter_keyword_documents", lambda *a: iter([
        {"id": "id", "tenant_id": "tenant", "source_doc": "x.pdf", "type": "email"}]))
    with pytest.raises(ValueError, match="unsafe staging keyword"):
        collection_admin.promote_staging("staging", tenant="tenant", execute=True)
    assert not target.writes


def test_api_and_cli_refuse(setup_promotion, monkeypatch, capsys):
    from fastapi import HTTPException
    from scripts import manage_documents
    from webapi.routers import ingestion

    source, _ = setup_promotion
    source.rows = [("id", {"tenant_id": "tenant", "type": "email", "source_doc": "mail:synthetic"})]
    monkeypatch.setattr(ingestion, "_request_scope", lambda request: None)
    monkeypatch.setattr(ingestion, "require_scope", lambda scope, tenant: None)
    with pytest.raises(HTTPException) as exc:
        ingestion.ingestion_promote(ingestion.PromoteRequest(collection="staging", tenant_id="tenant", execute=True))
    assert exc.value.status_code == 409
    assert manage_documents.main(["promote", "--collection", "staging", "--tenant", "tenant", "--execute"]) == 1
    assert "quarantined" in capsys.readouterr().err


@pytest.mark.parametrize("execute", [False, True])
def test_keyword_only_mail_document_blocks_promotion(setup_promotion, tmp_path, monkeypatch, execute):
    import json

    source, _ = setup_promotion
    source.rows = [("p", {"tenant_id": "tenant", "type": "pdf", "source_doc": "general.pdf"})]
    corpus = tmp_path / "staging.jsonl"
    corpus.write_text(json.dumps({"id": "tenant:email:abc:h1", "source_doc": "elsewhere.pdf"}), encoding="utf-8")
    monkeypatch.setattr(collection_admin, "_keyword_path", lambda _: str(corpus))
    monkeypatch.setattr(collection_admin, "_open", lambda name: source if name else pytest.fail("target accessed"))
    with pytest.raises(ValueError, match="unsafe staging keyword"):
        collection_admin.promote_staging("stage", tenant="tenant", execute=execute)
