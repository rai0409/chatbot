from __future__ import annotations

import json

from rag_grounded import Chunk
from rag_core import qa
from rag_core import retrieval
from rag_core.retrieval import RetrievedChunk


def _mk_chunk(label: str, text: str, score: float, page: int = 1) -> RetrievedChunk:
    return RetrievedChunk(
        text=text,
        metadata={"id": label, "source_doc": "doc", "source_pages": [page], "retrieval_source": "hybrid"},
        score=score,
    )


def test_answer_query_uses_reranker_in_chat_path(monkeypatch):
    base_hits = [
        _mk_chunk("A", "一般説明です。", 0.25, page=1),
        _mk_chunk("B", "PR2 の説明です。", 0.27, page=2),
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
    # Guard fired (soft_distance): no-answer responses carry no citations.
    assert res.citations == []
    assert "参考資料:" not in res.answer_with_footnotes
    assert "[S" not in res.answer_text
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
    monkeypatch.setattr(qa, "add_neighbor_chunks", lambda seeds, **kwargs: neighbor_hits)
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
        _mk_chunk("A", "一般説明です。", 0.25, page=1),
        _mk_chunk("B", "PR2 の説明です。", 0.27, page=2),
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
        "request_id",
        "original_query",
        "normalized_query",
        "question",
        "intent",
        "rewritten_query",
        "augmented_query",
        "before_rerank",
        "after_rerank",
        "grounded_candidate_chunk_ids",
        "final_guard_reason",
        "final_used_fallback",
        "answer_mode",
        "selected_context_chunk_ids",
        "selected_context_chars",
        "citations_count",
        "latency_ms",
    }
    assert isinstance(trace["request_id"], str) and trace["request_id"] != ""
    assert trace["original_query"] == "PR2 を教えて"
    assert trace["normalized_query"] == "PR2 を教えて"
    assert [ch.metadata["id"] for ch in trace["before_rerank"]] == ["A", "B"]
    assert [ch.metadata["id"] for ch in trace["after_rerank"]] == ["B", "A"]
    assert trace["final_guard_reason"] == "soft_distance"
    assert trace["final_used_fallback"] is True
    assert trace["answer_mode"] == "fallback"
    assert trace["grounded_candidate_chunk_ids"] == ["B", "A"]
    assert trace["selected_context_chunk_ids"] == ["B"]
    assert trace["selected_context_chars"] == len("PR2 の説明です。")
    assert isinstance(trace["citations_count"], int)
    assert isinstance(trace["latency_ms"], int)


def test_debug_retrieve_with_trace_does_not_call_chat_completion(monkeypatch):
    base_hits = [
        _mk_chunk("A", "パスワード再設定の手順です。", 0.25, page=1),
    ]
    calls = {"count": 0}

    def _fake_hybrid(*args, **kwargs):
        calls["count"] += 1
        return base_hits if calls["count"] == 1 else []

    class _ChatCompletions:
        def create(self, *args, **kwargs):
            raise AssertionError("chat completion must not be called")

    class _Client:
        chat = type("Chat", (), {"completions": _ChatCompletions()})()

    monkeypatch.setattr(qa, "hybrid_retrieve", _fake_hybrid)

    trace = qa.debug_retrieve_with_trace("パスワード再設定の方法は？", client=_Client(), top_k=5)

    assert calls["count"] == 2
    assert trace["answer_mode"] == "debug_retrieval_only"
    assert trace["citations_count"] == 0
    assert trace["query_type"] == "procedure"
    assert trace["final_guard_reason"] is None
    assert trace["final_used_fallback"] is False
    assert trace["selected_context_chunk_ids"] == ["A"]
    for list_name in ["before_rerank", "after_rerank", "after_parent_expansion"]:
        details = trace[list_name][0].metadata["score_details"]
        assert set(details) >= {
            "query_type",
            "keyword_score",
            "matched_terms",
            "matched_fields",
            "signals",
        }
        assert details["query_type"] == "procedure"


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


def test_guard_too_general_bypass_for_short_kanji_lookup_with_localized_top_evidence():
    retrieved = [
        _mk_chunk("A", "返金条件 は契約別に定義されています。", 0.25),
        _mk_chunk("B", "返金の一般説明です。", 0.27),
    ]
    chunks = [Chunk("A", "返金条件 は契約別に定義されています。", "doc", (1,), 0.25)]
    assert qa.guard_merged_top("返金条件", "other", chunks, retrieved) is None


def test_rerank_short_lookup_exact_match_bonus_promotes_exact_two_char_term():
    chunks = [
        _mk_chunk("A", "運用の一般説明です。", 0.25),
        _mk_chunk("B", "返金 は申請後に処理されます。", 0.27),
    ]
    reranked = qa.rerank_chunks("返金とは", chunks, intent="other")
    assert [c.metadata["id"] for c in reranked[:2]] == ["B", "A"]


def test_answer_path_expands_child_hit_to_parent_context(monkeypatch, tmp_path):
    parent_phrase = "親コンテキスト: 事前条件と注意事項を含む詳細な手順です。"
    rows = [
        {
            "id": "parent-ctx",
            "text": parent_phrase,
            "display_text": parent_phrase,
            "searchable_text": parent_phrase,
            "source_doc": "ops.pdf",
            "source_pages": [9],
            "doc_id": "ops.pdf",
            "chunk_index": 1,
            "searchable": 1,
            "type": "pdf",
            "quality": "high",
            "chunk_role": "parent",
            "child_chunk_ids": ["child-ctx"],
        },
        {
            "id": "child-ctx",
            "text": "子チャンク: 再設定ボタンを押します。",
            "display_text": "子チャンク: 再設定ボタンを押します。",
            "searchable_text": "再設定ボタン",
            "source_doc": "ops.pdf",
            "source_pages": [10],
            "doc_id": "ops.pdf",
            "chunk_index": 2,
            "searchable": 1,
            "type": "pdf",
            "quality": "high",
            "chunk_role": "child",
            "parent_chunk_id": "parent-ctx",
        },
    ]
    chunks_path = tmp_path / "chunks.jsonl"
    chunks_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(retrieval.config, "CHUNKS_JSONL_PATH", str(chunks_path))
    retrieval._INDEX_CACHE["path"] = None
    retrieval._INDEX_CACHE["mtime"] = None
    retrieval._INDEX_CACHE["index"] = None

    child_hit = RetrievedChunk(
        text="子チャンク: 再設定ボタンを押します。",
        metadata={
            "id": "child-ctx",
            "parent_chunk_id": "parent-ctx",
            "source_doc": "ops.pdf",
            "source_pages": [10],
            "retrieval_source": "hybrid",
        },
        score=0.24,
    )
    calls = {"count": 0}

    def _fake_hybrid(*args, **kwargs):
        calls["count"] += 1
        return [child_hit] if calls["count"] == 1 else []

    monkeypatch.setattr(qa, "hybrid_retrieve", _fake_hybrid)
    monkeypatch.setattr(qa, "guard_merged_top", lambda *args, **kwargs: "soft_distance")

    result, trace = qa.answer_query_with_trace("再設定の方法", client=object(), top_k=5, intent_override="procedure")
    assert [it.metadata["id"] for it in result.retrieved] == ["child-ctx"]
    assert trace["after_rerank"][0].metadata["id"] == "child-ctx"
    assert parent_phrase in "\n".join(trace.get("selected_context_preview", []))
