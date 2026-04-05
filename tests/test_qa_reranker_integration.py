from __future__ import annotations

from rag_grounded import Chunk
from rag_core import qa
from rag_core.retrieval import RetrievedChunk


def _mk_chunk(label: str, text: str, score: float) -> RetrievedChunk:
    return RetrievedChunk(
        text=text,
        metadata={"id": label, "source_doc": "doc", "source_pages": [1], "retrieval_source": "hybrid"},
        score=score,
    )


def test_answer_query_uses_reranker_in_chat_path(monkeypatch):
    base_hits = [
        _mk_chunk("A", "一般説明です。", 0.25),
        _mk_chunk("B", "PR2 の説明です。", 0.27),
    ]
    aug_hits = []
    calls = {"count": 0}

    def _fake_hybrid(*args, **kwargs):
        calls["count"] += 1
        return base_hits if calls["count"] == 1 else aug_hits

    monkeypatch.setattr(qa, "hybrid_retrieve", _fake_hybrid)
    monkeypatch.setattr(qa, "rerank_chunks", lambda question, chunks, intent=None: list(reversed(chunks)))
    monkeypatch.setattr(qa, "guard_merged_top", lambda *args, **kwargs: "soft_distance")

    res = qa.answer_query("PR2 を教えて", client=object(), top_k=5)
    assert [it.metadata["id"] for it in res.retrieved] == ["B", "A"]
    assert all("retrieval_source" in it.metadata for it in res.retrieved)
    assert "参考資料:" in res.answer_with_footnotes
    assert "[1]" in res.answer_with_footnotes
    if res.citations:
        assert set(res.citations[0].__dict__.keys()) == {"number", "source_doc", "source_pages", "chunk_id"}
    assert set(res.to_dict().keys()) == {
        "answer_text",
        "answer_with_footnotes",
        "intent",
        "guard_reason",
        "used_fallback",
        "citations",
        "retrieved",
        "rewritten_query",
        "augmented_query",
    }


def test_retrieve_chunks_vector_only_path_remains_unchanged(monkeypatch):
    vector_hits = [
        _mk_chunk("A", "先頭", 0.11),
        _mk_chunk("B", "次点", 0.12),
    ]
    monkeypatch.setattr(qa, "vector_retrieve", lambda *args, **kwargs: vector_hits)
    out = qa.retrieve_chunks("query", client=object(), top_k=2)
    assert [it.metadata["id"] for it in out] == ["A", "B"]


def test_answer_query_procedure_neighbor_context_keeps_stable_order_under_weak_evidence(monkeypatch):
    base_hits = [
        _mk_chunk("A", "設定変更の前提条件です。", 0.25),
        _mk_chunk("B", "補足の操作説明です。", 0.27),
    ]
    aug_hits = []
    neighbor_hits = [
        _mk_chunk("A", "設定変更の前提条件です。", 0.25),
        _mk_chunk("B", "補足の操作説明です。", 0.27),
        _mk_chunk("C", "近傍チャンク: 画面遷移の補足です。", 0.28),
        _mk_chunk("D", "近傍チャンク: 注意事項です。", 0.29),
    ]
    calls = {"count": 0}

    def _fake_hybrid(*args, **kwargs):
        calls["count"] += 1
        return base_hits if calls["count"] == 1 else aug_hits

    monkeypatch.setattr(qa, "hybrid_retrieve", _fake_hybrid)
    monkeypatch.setattr(qa, "add_neighbor_chunks", lambda seeds: neighbor_hits)
    monkeypatch.setattr(qa, "guard_merged_top", lambda *args, **kwargs: "soft_distance")

    res = qa.answer_query(
        "設定を変更する方法を教えて",
        client=object(),
        top_k=5,
        intent_override="procedure",
    )
    assert res.intent == "procedure"
    assert res.guard_reason == "soft_distance"
    assert [it.metadata["id"] for it in res.retrieved] == ["A", "B", "C", "D"]
    assert all(it.metadata.get("retrieval_source") == "hybrid" for it in res.retrieved)


def test_answer_query_with_trace_matches_answer_query_and_exposes_core_trace(monkeypatch):
    base_hits = [
        _mk_chunk("A", "一般説明です。", 0.25),
        _mk_chunk("B", "PR2 の説明です。", 0.27),
    ]
    aug_hits = []
    calls = {"count": 0}

    def _fake_hybrid(*args, **kwargs):
        calls["count"] += 1
        return base_hits if calls["count"] % 2 == 1 else aug_hits

    monkeypatch.setattr(qa, "hybrid_retrieve", _fake_hybrid)
    monkeypatch.setattr(qa, "rerank_chunks", lambda question, chunks, intent=None: list(reversed(chunks)))
    monkeypatch.setattr(qa, "guard_merged_top", lambda *args, **kwargs: "soft_distance")

    plain = qa.answer_query("PR2 を教えて", client=object(), top_k=5)
    traced, trace = qa.answer_query_with_trace("PR2 を教えて", client=object(), top_k=5)

    assert plain.to_dict() == traced.to_dict()
    assert set(traced.to_dict().keys()) == {
        "answer_text",
        "answer_with_footnotes",
        "intent",
        "guard_reason",
        "used_fallback",
        "citations",
        "retrieved",
        "rewritten_query",
        "augmented_query",
    }

    assert set(trace.keys()) >= {
        "question",
        "intent",
        "rewritten_query",
        "augmented_query",
        "before_rerank",
        "after_rerank",
        "final_guard_reason",
        "final_used_fallback",
    }
    assert [ch.metadata["id"] for ch in trace["before_rerank"]] == ["A", "B"]
    assert [ch.metadata["id"] for ch in trace["after_rerank"]] == ["B", "A"]
    assert trace["final_guard_reason"] == "soft_distance"
    assert trace["final_used_fallback"] is True


def test_guard_too_general_keeps_vague_short_query():
    retrieved = [
        _mk_chunk("A", "一般説明です。", 0.25),
        _mk_chunk("B", "補足説明です。", 0.27),
    ]
    chunks = [Chunk("A", "一般説明です。", "doc", (1,), 0.25)]
    assert qa.guard_merged_top("意味は？", "other", chunks, retrieved) == "too_general"


def test_guard_too_general_bypass_for_short_quoted_code_like_query():
    retrieved = [
        _mk_chunk("A", "ABC123 の仕様です。", 0.25),
        _mk_chunk("B", "XABC123Y の一般仕様です。", 0.27),
    ]
    chunks = [Chunk("A", "ABC123 の仕様です。", "doc", (1,), 0.25)]
    assert qa.guard_merged_top('"ABC123" の仕様', "other", chunks, retrieved) is None


def test_guard_too_general_bypass_for_short_glossary_style_japanese_term():
    retrieved = [
        _mk_chunk("A", "カタカナ語 は外来語の表記です。", 0.25),
        _mk_chunk("B", "カタカナ表記の一般説明です。", 0.27),
    ]
    chunks = [Chunk("A", "カタカナ語 は外来語の表記です。", "doc", (1,), 0.25)]
    assert qa.guard_merged_top("カタカナ語 の意味", "other", chunks, retrieved) is None
