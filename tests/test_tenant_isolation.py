from __future__ import annotations

import json
from types import SimpleNamespace

import config
from rag_core import answer_cache, retrieval
from rag_core.retrieval import RetrievedChunk
from webapi import main


def _write_corpus(path, rows):
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _setup_corpus(monkeypatch, tmp_path):
    corpus = tmp_path / "chunks.jsonl"
    _write_corpus(
        corpus,
        [
            {"id": "legacy-1", "text": "共通の説明 レガシー本文"},
            {"id": "default-1", "text": "共通の説明 デフォルト本文", "tenant_id": "default"},
            {"id": "tenant-b-1", "text": "共通の説明 テナントB本文", "tenant_id": "tenant_b"},
        ],
    )
    monkeypatch.setattr(config, "CHUNKS_JSONL_PATH", str(corpus))
    retrieval._INDEX_CACHE.update({"path": None, "mtime": None, "index": None})
    retrieval._KEYWORD_INDEX_WARNED_KEYS.clear()
    return corpus


def test_keyword_default_tenant_sees_legacy_and_default_only(monkeypatch, tmp_path):
    _setup_corpus(monkeypatch, tmp_path)

    hits = retrieval.keyword_retrieve("共通の説明", top_k=10)

    ids = {h.metadata["id"] for h in hits}
    assert ids == {"legacy-1", "default-1"}


def test_keyword_non_default_tenant_sees_only_its_chunks(monkeypatch, tmp_path):
    _setup_corpus(monkeypatch, tmp_path)

    hits = retrieval.keyword_retrieve("共通の説明", top_k=10, tenant_id="tenant_b")

    ids = {h.metadata["id"] for h in hits}
    assert ids == {"tenant-b-1"}


class _FakeVectorCollection:
    def __init__(self):
        self.where_clauses = []

    def query(self, query_embeddings, n_results, where=None):
        self.where_clauses.append(dict(where or {}))
        return {
            "documents": [["レガシー本文", "デフォルト本文", "テナントB本文"]],
            "metadatas": [
                [
                    {"id": "legacy-1", "source_doc": "d", "chunk_role": "child"},
                    {"id": "default-1", "source_doc": "d", "chunk_role": "child", "tenant_id": "default"},
                    {"id": "tenant-b-1", "source_doc": "d", "chunk_role": "child", "tenant_id": "tenant_b"},
                ]
            ],
            "distances": [[0.1, 0.2, 0.3]],
        }


def test_vector_tenant_filtering(monkeypatch):
    fake = _FakeVectorCollection()
    monkeypatch.setattr(retrieval.store, "get_vectorstore", lambda *a, **k: fake)
    monkeypatch.setattr(
        retrieval.embedder, "embed_queries", lambda texts, client=None: [[0.1] for _ in texts]
    )

    default_hits = retrieval.vector_retrieve("共通の説明", None, top_k=10)
    assert {h.metadata["id"] for h in default_hits} == {"legacy-1", "default-1"}
    assert "tenant_id" not in fake.where_clauses[0]

    tenant_b_hits = retrieval.vector_retrieve("共通の説明", None, top_k=10, tenant_id="tenant_b")
    assert {h.metadata["id"] for h in tenant_b_hits} == {"tenant-b-1"}
    assert fake.where_clauses[1]["tenant_id"] == "tenant_b"


def test_parent_expansion_does_not_cross_tenants(monkeypatch, tmp_path):
    corpus = tmp_path / "chunks.jsonl"
    _write_corpus(
        corpus,
        [
            {
                "id": "parent-b",
                "text": "テナントBの親本文",
                "tenant_id": "tenant_b",
                "chunk_role": "parent",
            },
        ],
    )
    monkeypatch.setattr(config, "CHUNKS_JSONL_PATH", str(corpus))
    retrieval._INDEX_CACHE.update({"path": None, "mtime": None, "index": None})
    retrieval._KEYWORD_INDEX_WARNED_KEYS.clear()

    child = RetrievedChunk(
        text="子チャンク本文",
        metadata={"id": "child-1", "parent_chunk_id": "parent-b", "tenant_id": "default"},
        score=0.2,
    )

    out = retrieval.expand_parent_chunks([child], tenant_id="default")

    assert len(out) == 1
    assert out[0].metadata["id"] == "child-1"  # parent from tenant_b not pulled in


def test_neighbor_lookup_filters_other_tenants(monkeypatch):
    class _FakeGetCollection:
        def get(self, where=None):
            return {
                "documents": ["隣接チャンク"],
                "metadatas": [{"id": "n1", "doc_id": "d", "chunk_index": 2, "tenant_id": "tenant_b"}],
            }

    monkeypatch.setattr(retrieval.store, "get_vectorstore", lambda *a, **k: _FakeGetCollection())
    seed = RetrievedChunk(
        text="シード", metadata={"id": "s1", "doc_id": "d", "chunk_index": 1}, score=0.2
    )

    out = retrieval.add_neighbor_chunks([seed], tenant_id="default")

    assert [c.metadata["id"] for c in out] == ["s1"]


def test_chat_threads_tenant_to_pipeline_and_cache(monkeypatch, tmp_path):
    chunks = tmp_path / "chunks.jsonl"
    chunks.write_text('{"id":"c1","text":"本文"}\n', encoding="utf-8")
    monkeypatch.setattr(config, "CHUNKS_JSONL_PATH", str(chunks))
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", False)
    monkeypatch.setattr(config, "ANSWER_CACHE_ENABLED", True)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(main, "ensure_openai_client", lambda base_url=None: object())
    answer_cache.clear()

    seen_tenants = []

    def fake(question, client=None, top_k=20, max_context_chars=8000, intent_override=None, tenant_id="default"):
        seen_tenants.append(tenant_id)
        from schemas import AnswerResult

        ans = AnswerResult(
            answer_text=f"{tenant_id} の回答",
            answer_with_footnotes=f"{tenant_id} の回答",
            intent="other",
            guard_reason=None,
            used_fallback=False,
            citations=[],
            retrieved=[],
            rewritten_query="",
            augmented_query="",
        )
        trace = {
            "request_id": "r1",
            "answer_mode": "grounded",
            "final_guard_reason": None,
            "final_used_fallback": False,
            "citations_count": 0,
            "latency_ms": 1,
            "after_rerank": [],
        }
        return ans, trace

    monkeypatch.setattr(main, "answer_query_with_trace", fake)

    first_a = main.chat(main.ChatRequest(question="質問です", tenant_id="tenant_a"))
    first_b = main.chat(main.ChatRequest(question="質問です", tenant_id="tenant_b"))
    second_a = main.chat(main.ChatRequest(question="質問です", tenant_id="tenant_a"))

    assert seen_tenants == ["tenant_a", "tenant_b"]  # third call was a cache hit
    assert first_a["answer_text"] == "tenant_a の回答"
    assert first_b["answer_text"] == "tenant_b の回答"
    assert second_a == first_a
    assert answer_cache.size() == 2  # one entry per tenant, never shared


def test_chat_approved_lookup_respects_tenant(monkeypatch, tmp_path):
    records = [
        {
            "qa_id": "qa-a",
            "question": "営業時間を教えてください",
            "approved_answer": "テナントA: 9時から17時です。",
            "status": "approved",
            "tenant_id": "tenant_a",
            "approved_citations": [{"source_doc": "a.pdf", "source_pages": [1]}],
        },
        {
            "qa_id": "qa-b",
            "question": "営業時間を教えてください",
            "approved_answer": "テナントB: 10時から18時です。",
            "status": "approved",
            "tenant_id": "tenant_b",
            "approved_citations": [{"source_doc": "b.pdf", "source_pages": [1]}],
        },
    ]
    path = tmp_path / "approved.jsonl"
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", True)
    monkeypatch.setattr(config, "APPROVED_QA_PATH", str(path))
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(main, "_approved_qa_index", None)
    monkeypatch.setattr(main, "_approved_qa_index_path", None)

    payload_a = main.chat(main.ChatRequest(question="営業時間を教えてください", tenant_id="tenant_a"))
    payload_b = main.chat(main.ChatRequest(question="営業時間を教えてください", tenant_id="tenant_b"))

    assert payload_a["approved_qa_id"] == "qa-a"
    assert payload_a["answer_text"].startswith("テナントA")
    assert payload_b["approved_qa_id"] == "qa-b"
    assert payload_b["answer_text"].startswith("テナントB")


def test_cache_key_includes_tenant():
    key_a = answer_cache.build_key("質問です", top_k=5, max_context_chars=8000, tenant_id="tenant_a")
    key_b = answer_cache.build_key("質問です", top_k=5, max_context_chars=8000, tenant_id="tenant_b")
    key_default = answer_cache.build_key("質問です", top_k=5, max_context_chars=8000)
    key_blank = answer_cache.build_key("質問です", top_k=5, max_context_chars=8000, tenant_id="  ")

    assert key_a != key_b
    assert key_default == key_blank
