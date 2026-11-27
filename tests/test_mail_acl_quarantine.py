"""Fail-closed mail quarantine. Synthetic test data only."""
from __future__ import annotations

import json

import config
from rag_core.mail_access import is_private_mail_record


def test_mail_classification_and_legacy_documents():
    assert is_private_mail_record({"type": "email"})
    assert is_private_mail_record({"source_type": "EMAIL"})
    assert is_private_mail_record({"doc_type": "email"})
    assert is_private_mail_record({"source_doc": "mail:abc"})
    assert is_private_mail_record({"id": "tenant:email:" + "a" * 24 + ":h" + "b" * 12})
    assert is_private_mail_record({"type": "mail"}) and is_private_mail_record({"doc_type": "EML"})
    assert is_private_mail_record({"parser": "mail-ingest-v2"})
    assert not is_private_mail_record({"source_doc": "legacy.pdf"})
    assert not is_private_mail_record({"id": "legacy", "text": "public"})


def test_general_documents_named_email_are_not_mail():
    # An xlsx sheet named "email" and a PDF with doc_id "email" are general documents.
    assert not is_private_mail_record({"id": "tenant:xlsx:book.xlsx:email:r2:h0123456789ab", "type": "xlsx"})
    assert not is_private_mail_record({"id": "tenant:xlsx:book.xlsx:Email:r2:h0123456789ab"})
    assert not is_private_mail_record({"id": "tenant:email:ja:p1:c0", "doc_id": "email", "type": "pdf"})
    assert is_private_mail_record({"id": "tenant:email:abc:h1"})


def test_keyword_index_private_mail_not_loaded_or_citable(tmp_path, monkeypatch):
    from rag_core import retrieval

    path = tmp_path / "chunks.jsonl"
    rows = [
        {"id": "mail", "tenant_id": "acme", "source_doc": "mail:secret",
         "type": "email", "searchable": 1, "text": "secretmailterm"},
        {"id": "public", "tenant_id": "acme", "source_doc": "public.pdf",
         "type": "pdf", "searchable": 1, "text": "publiccontractterm"},
        {"id": "legacy", "tenant_id": "acme", "source_doc": "old.pdf",
         "searchable": 1, "text": "legacycontractterm"},
    ]
    path.write_text("".join(json.dumps(x) + "\n" for x in rows), encoding="utf-8")
    monkeypatch.setattr(config, "CHUNKS_JSONL_PATH", str(path))
    monkeypatch.setattr(config, "INDEX_DIR", str(tmp_path))
    monkeypatch.setattr(config, "KEYWORD_TOKENIZER", "heuristic")
    monkeypatch.setattr(config, "KEYWORD_INDEX_ENGINE", "inverted")
    monkeypatch.setattr(config, "IGNORE_SEARCHABLE", False)
    index = retrieval._load_keyword_index()
    assert len(index) == 2
    assert "secretmailterm" not in index.vocabulary
    assert retrieval.chunk_row("mail") is None
    assert retrieval.chunk_row("public")["text"] == "publiccontractterm"
    hits = retrieval.keyword_retrieve("publiccontractterm", 10, tenant_id="acme")
    assert {x.metadata["id"] for x in hits} == {"public", "legacy"}
    private_hits = retrieval.keyword_retrieve(
        "secretmailterm", 10, tenant_id="acme"
    )
    assert all(
        hit.metadata["id"] in {"public", "legacy"}
        and not is_private_mail_record(hit.metadata)
        and "secretmailterm" not in hit.text
        for hit in private_hits
    )


def test_vector_results_filter_mail_without_changing_legacy_where(monkeypatch):
    from rag_core import retrieval
    monkeypatch.setenv("VECTOR_BACKEND", "chroma")

    class FakeStore:
        def query(self, query_embeddings, n_results, where):
            return {"ids": [["m", "p", "l"]], "documents": [["mail secret", "public text", "legacy text"]],
                    "metadatas": [[
                        {"id": "m", "tenant_id": "acme", "type": "email", "source_doc": "mail:abc"},
                        {"id": "p", "tenant_id": "acme", "type": "pdf", "source_doc": "pub.pdf"},
                        {"id": "l", "tenant_id": "acme", "source_doc": "old.pdf"},
                    ]], "distances": [[0.01, 0.1, 0.2]]}

    monkeypatch.setattr(retrieval.store, "get_vectorstore", lambda **kw: FakeStore())
    monkeypatch.setattr(retrieval.embedder, "embed_queries", lambda texts, client=None: [[0.1]])
    hits = retrieval.vector_retrieve("any", client=None, top_k=10, tenant_id="acme")
    assert {x.metadata["id"] for x in hits} == {"p", "l"}


def test_qdrant_vector_prefilter_preserves_legacy_documents(monkeypatch):
    from rag_core import retrieval
    monkeypatch.setenv("VECTOR_BACKEND", "qdrant")
    seen = {}

    class FakeStore:
        def query(self, query_embeddings, n_results, where):
            seen["where"] = where
            return {"ids": [["p"]], "documents": [["public"]],
                    "metadatas": [[{"id": "p", "type": "pdf", "tenant_id": "acme"}]],
                    "distances": [[0.1]]}

    monkeypatch.setattr(retrieval.store, "get_vectorstore", lambda **kw: FakeStore())
    monkeypatch.setattr(retrieval.embedder, "embed_queries", lambda texts, client=None: [[0.1]])
    hits = retrieval.vector_retrieve("any", client=None, top_k=5, tenant_id="acme")
    assert [x.metadata["id"] for x in hits] == ["p"]
    clauses = seen["where"].get("$and", [seen["where"]])
    assert {"type": {"$ne": "email"}} in clauses


def test_parent_and_neighbor_cannot_reintroduce_mail(tmp_path, monkeypatch):
    from rag_core import retrieval
    corpus = tmp_path / "chunks.jsonl"
    corpus.write_text(json.dumps({"id": "parent", "text": "sensitive",
                                  "type": "email", "source_doc": "mail:abc",
                                  "tenant_id": "acme"}) + "\n", encoding="utf-8")
    monkeypatch.setattr(config, "CHUNKS_JSONL_PATH", str(corpus))
    monkeypatch.setattr(config, "INDEX_DIR", str(tmp_path))
    monkeypatch.setattr(config, "KEYWORD_TOKENIZER", "heuristic")
    public = retrieval.RetrievedChunk("public", {"id": "seed", "parent_chunk_id": "parent", "tenant_id": "acme",
                                                  "doc_id": "pub.pdf", "chunk_index": 1}, 0.1)
    mail = retrieval.RetrievedChunk("private", {"type": "email", "source_doc": "mail:abc", "tenant_id": "acme"}, 0.0)
    expanded = retrieval.expand_parent_chunks([public, mail], tenant_id="acme")
    assert [x.text for x in expanded] == ["public"]

    class FakeStore:
        def get(self, where):
            return {"ids": ["tenant:email:abc:h1", "p2"], "documents": ["private", "public neighbor"],
                    "metadatas": [
                        # Neighbors are siblings of the seed's section (same parent_chunk_id, 2026-11-27).
                        {"tenant_id": "acme", "type": "pdf", "doc_id": "pub.pdf", "chunk_index": 2, "parent_chunk_id": "parent"},
                        {"id": "p2", "tenant_id": "acme", "type": "pdf", "doc_id": "pub.pdf", "chunk_index": 2,
                         "parent_chunk_id": "parent"},
                    ]}

    monkeypatch.setattr(retrieval.store, "get_vectorstore", lambda **kw: FakeStore())
    result = retrieval.add_neighbor_chunks([mail, public], window=1, tenant_id="acme")
    assert [x.text for x in result] == ["public", "public neighbor"]


def test_citation_api_cannot_return_private_mail(monkeypatch):
    from fastapi import HTTPException
    from webapi.routers import chat_source
    from rag_core import retrieval
    monkeypatch.setattr(retrieval, "chunk_row", lambda *a, **kw: {
        "id": "m1", "source_doc": "mail:secret", "tenant_id": "acme",
        "text": "highly private mail",
    })
    monkeypatch.setattr(chat_source, "_resolve_staging_collection", lambda *a, **kw: "test_stage")
    try:
        chat_source.chat_source("m1", tenant_id="acme", collection="test_stage")
    except HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("private email was returned")


def test_unverifiable_store_responses_and_conflicting_markers(monkeypatch):
    from rag_core import approved_similar, mail_access, retrieval

    monkeypatch.setattr(mail_access, "MAIL_SEARCH_ENABLED", True)
    mail = "tenant:email:abc:h1"
    public = {"tenant_id": "default", "doc_type": "approved_qa_pair", "chunk_type": "qa_pair", "qa_id": "q"}
    response = {}
    class Collection:
        def query(self, **kwargs):
            return response

    collection = Collection()
    monkeypatch.setattr(retrieval.store, "get_vectorstore", lambda **kwargs: collection)
    monkeypatch.setattr(retrieval.embedder, "embed_queries", lambda *args, **kwargs: [[1.0]])
    for ids, meta in [(None, public), ([[]], public), ([[mail]], public),
                      ([["public"]], {**public, "id": mail}), ([["public"]], {**public, "parser": "mail-ingest"})]:
        response.clear()
        response.update(documents=[["private"]], distances=[[0.01]], metadatas=[[meta]])
        if ids is not None:
            response["ids"] = ids
        assert not retrieval.vector_retrieve("q", None, 1)
        assert not approved_similar.search_approved_similar_candidates("q", collection=collection)


def test_chunk_lookup_uses_requested_id(monkeypatch):
    from types import SimpleNamespace
    from rag_core import retrieval
    from webapi.routers import chat_source
    import pytest
    from fastapi import HTTPException

    row = {"id": "public", "type": "pdf", "tenant_id": "acme", "text": "private"}
    monkeypatch.setattr(retrieval, "_index_for_collection", lambda _: SimpleNamespace(row=lambda _: row))
    mail = "tenant:email:abc:h1"
    assert retrieval.chunk_row(mail) is None
    monkeypatch.setattr(retrieval, "chunk_row", lambda *args: row)
    monkeypatch.setattr(chat_source, "_resolve_staging_collection", lambda *args, **kwargs: "stage")
    with pytest.raises(HTTPException) as exc:
        chat_source.chat_source(mail, tenant_id="acme", collection="stage")
    assert exc.value.status_code == 404


def test_widen_and_tenant_retry_check_actual_ids(monkeypatch):
    from rag_core import retrieval

    monkeypatch.setenv("VECTOR_BACKEND", "chroma")
    monkeypatch.setattr(config, "TENANT_FILTER_STRICT", False)
    monkeypatch.setattr(config, "CHILD_RETRIEVAL_OVERSAMPLE", 1)
    windows = []

    class Store:
        def query(self, n_results, where, **kwargs):
            strict = {"tenant_id": "default"} in where.get("$and", [where])
            windows.append((n_results, strict))
            if strict:
                rows = [("tenant:email:abc:h1", {"type": "pdf"}, 0.05), ("public", {}, 0.2)]
            elif n_results == 1:
                rows = [("tenant:email:abc:h1", {}, 0.05)]
            else:
                rows = [(f"other-{i}", {"tenant_id": "other"}, 0.1) for i in range(n_results)]
            return {"ids": [[r[0] for r in rows]], "metadatas": [[r[1] for r in rows]],
                    "documents": [[r[0] for r in rows]], "distances": [[r[2] for r in rows]]}

    monkeypatch.setattr(retrieval.store, "get_vectorstore", lambda **kw: Store())
    hits = retrieval.vector_retrieve("q", None, 1, query_embedding=[1.0])
    assert [(row.metadata["id"], row.score) for row in hits] == [("public", 0.2)]
    assert windows == [(1, False), (4, False), (4, True)]


def test_duplicate_store_id_makes_correspondence_ambiguous(monkeypatch):
    from rag_core import retrieval

    class Collection:
        def query(self, **kwargs):
            return {"ids": [["same", "same"]], "metadatas": [[{}, {"type": "email"}]],
                    "documents": [["private", "private"]], "distances": [[0.1, 0.2]]}

    monkeypatch.setattr(retrieval.store, "get_vectorstore", lambda **kw: Collection())
    assert retrieval.vector_retrieve("q", None, 2, query_embedding=[1.0]) == []
