import uuid

import pytest

from scripts.approved_qa_to_canonical_jsonl import approved_qa_record_to_canonical_row


def _record(tenant="default", qa_id="qa_x"):
    return {
        "qa_id": qa_id,
        "question": "営業時間は？",
        "approved_answer": "9時から17時です。",
        "approved_citations": [{"source_doc": "faq.pdf", "source_pages": [1], "title": "FAQ"}],
        "tenant_id": tenant,
        "doc_version": "v1",
        "status": "approved",
    }


def test_default_tenant_keeps_the_historical_pair_id():
    assert approved_qa_record_to_canonical_row(_record())["id"] == "approved_qa_pair:qa_x"


def test_other_tenants_get_a_prefixed_pair_id():
    row = approved_qa_record_to_canonical_row(_record(tenant="tenant_a"))
    assert row["id"] == "tenant_a:approved_qa_pair:qa_x" and row["tenant_id"] == "tenant_a"


def test_two_tenants_can_hold_the_same_qa_id(monkeypatch, tmp_path):
    qdrant_client = pytest.importorskip("qdrant_client")
    import config
    from rag_core.vector_backend import QdrantCollection
    from scripts import ingest_canonical_jsonl as ingest

    monkeypatch.setattr(config, "VECTORSTORE_DIR", str(tmp_path))
    col = QdrantCollection(f"pair_{uuid.uuid4().hex[:8]}", client=qdrant_client.QdrantClient(":memory:"))
    monkeypatch.setattr(ingest.store, "get_vectorstore", lambda **kwargs: col)
    monkeypatch.setattr(ingest.embedder, "is_local_provider", lambda: True)
    monkeypatch.setattr(ingest.embedder, "embed_documents", lambda texts, client=None: [[1.0, 0.0] for _ in texts])
    for tenant in ("tenant_a", "tenant_b"):
        ingest.ingest_canonical_rows([approved_qa_record_to_canonical_row(_record(tenant=tenant))])
    assert sorted(col.get(include=[])["ids"]) == [
        "tenant_a:approved_qa_pair:qa_x",
        "tenant_b:approved_qa_pair:qa_x",
    ]


@pytest.mark.parametrize(
    "chunk_id, tenant, expected",
    [
        ("approved_qa_pair:q1", "tenant_a", "q1"),
        ("tenant_a:approved_qa_pair:q1", "tenant_a", "q1"),
        ("tenant_b:approved_qa_pair:q1", "tenant_a", None),
        ("approved_qa_pair:", "tenant_a", None),
        ("c1", "tenant_a", None),
    ],
)
def test_chat_source_parses_pair_ids_of_the_callers_tenant_only(chunk_id, tenant, expected):
    from webapi import main

    assert main._approved_pair_qa_id(chunk_id, tenant) == expected


def test_eval_cases_use_the_tenant_prefixed_pair_id():
    from scripts.build_eval_cases_from_approved_qa import build_cases

    record = _record(tenant="tenant_a")
    pair = approved_qa_record_to_canonical_row(record)
    cases, skipped = build_cases(approved_records=[record], pair_chunks=[pair])
    assert "missing_pair_chunk" not in skipped
    assert cases[0]["case_id"] == "real_q_tenant_a_qa_x"
    assert cases[0]["gold_chunk_ids"] == ["tenant_a:approved_qa_pair:qa_x"]


def test_exact_index_item_id_falls_back_to_the_tenant_prefixed_pair_id():
    from tools.build_approved_qa_exact_index import canonical_to_item

    assert canonical_to_item({"qa_id": "qa_x", "tenant_id": "tenant_a", "question": "q", "approved_answer": "a"})["id"] == (
        "tenant_a:approved_qa_pair:qa_x"
    )
    assert canonical_to_item({"qa_id": "qa_x", "question": "q", "approved_answer": "a"})["id"] == "approved_qa_pair:qa_x"
