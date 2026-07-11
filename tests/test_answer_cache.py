from __future__ import annotations

import json
import os

import config
from rag_core import answer_cache
from schemas import AnswerResult
from webapi import main


def _fake_pipeline(calls, *, guard_reason=None, used_fallback=False):
    def fake(question, client=None, top_k=20, max_context_chars=8000, intent_override=None, **kwargs):
        calls.append(question)
        ans = AnswerResult(
            answer_text=f"回答本文 {len(calls)}",
            answer_with_footnotes=f"回答本文 {len(calls)}\n参考資料:\n- [1] doc.pdf p1",
            intent="other",
            guard_reason=guard_reason,
            used_fallback=used_fallback,
            citations=[],
            retrieved=[],
            rewritten_query="",
            augmented_query="",
        )
        trace = {
            "request_id": f"req-{len(calls)}",
            "normalized_query": question,
            "intent": "other",
            "final_guard_reason": guard_reason,
            "final_used_fallback": used_fallback,
            "citations_count": 0,
            "latency_ms": 1,
            "after_rerank": [],
        }
        return ans, trace

    return fake


def _setup_chat(monkeypatch, tmp_path, *, enabled, guard_reason=None, used_fallback=False):
    chunks = tmp_path / "chunks.jsonl"
    chunks.write_text('{"id":"c1","text":"本文"}\n', encoding="utf-8")
    monkeypatch.setattr(config, "CHUNKS_JSONL_PATH", str(chunks))
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", False)
    monkeypatch.setattr(config, "ANSWER_CACHE_ENABLED", enabled)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(main, "ensure_openai_client", lambda base_url=None: object())
    answer_cache.clear()
    calls = []
    monkeypatch.setattr(
        main,
        "answer_query_with_trace",
        _fake_pipeline(calls, guard_reason=guard_reason, used_fallback=used_fallback),
    )
    return calls, chunks


def test_disabled_by_default_runs_pipeline_each_time(monkeypatch, tmp_path):
    calls, _ = _setup_chat(monkeypatch, tmp_path, enabled=False)

    main.chat(main.ChatRequest(question="質問です"))
    main.chat(main.ChatRequest(question="質問です"))

    assert len(calls) == 2
    assert answer_cache.size() == 0


def test_enabled_second_call_hits_cache(monkeypatch, tmp_path):
    calls, _ = _setup_chat(monkeypatch, tmp_path, enabled=True)

    first = main.chat(main.ChatRequest(question="質問です"))
    second = main.chat(main.ChatRequest(question="質問です"))

    assert len(calls) == 1
    assert second == first
    assert answer_cache.size() == 1


def test_guard_responses_are_never_cached(monkeypatch, tmp_path):
    calls, _ = _setup_chat(
        monkeypatch, tmp_path, enabled=True, guard_reason="soft_distance", used_fallback=True
    )

    main.chat(main.ChatRequest(question="質問です"))
    main.chat(main.ChatRequest(question="質問です"))

    assert len(calls) == 2
    assert answer_cache.size() == 0


def test_extractive_fallback_responses_are_never_cached(monkeypatch, tmp_path):
    calls, _ = _setup_chat(
        monkeypatch, tmp_path, enabled=True, guard_reason=None, used_fallback=True
    )

    main.chat(main.ChatRequest(question="質問です"))
    main.chat(main.ChatRequest(question="質問です"))

    assert len(calls) == 2
    assert answer_cache.size() == 0


def test_corpus_mtime_change_invalidates_key(monkeypatch, tmp_path):
    calls, chunks = _setup_chat(monkeypatch, tmp_path, enabled=True)

    main.chat(main.ChatRequest(question="質問です"))
    stat = os.stat(chunks)
    os.utime(chunks, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    main.chat(main.ChatRequest(question="質問です"))

    assert len(calls) == 2


def test_missing_to_present_corpus_changes_key(monkeypatch, tmp_path):
    missing = tmp_path / "not_yet.jsonl"
    monkeypatch.setattr(config, "CHUNKS_JSONL_PATH", str(missing))
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", False)
    monkeypatch.setattr(config, "ANSWER_CACHE_ENABLED", True)

    key_missing = answer_cache.build_key("質問です", top_k=5, max_context_chars=8000)
    missing.write_text('{"id":"c1","text":"本文"}\n', encoding="utf-8")
    key_present = answer_cache.build_key("質問です", top_k=5, max_context_chars=8000)

    assert key_missing != key_present


def test_lru_eviction_at_max_entries(monkeypatch, tmp_path):
    chunks = tmp_path / "chunks.jsonl"
    chunks.write_text('{"id":"c1","text":"本文"}\n', encoding="utf-8")
    monkeypatch.setattr(config, "CHUNKS_JSONL_PATH", str(chunks))
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", False)
    monkeypatch.setattr(config, "ANSWER_CACHE_ENABLED", True)
    monkeypatch.setattr(config, "ANSWER_CACHE_MAX_ENTRIES", 2)
    answer_cache.clear()

    keys = [
        answer_cache.build_key(q, top_k=5, max_context_chars=8000)
        for q in ("質問一", "質問二", "質問三")
    ]
    for idx, key in enumerate(keys):
        answer_cache.put(key, {"answer_text": f"回答{idx}"})

    assert answer_cache.size() == 2
    assert answer_cache.get(keys[0]) is None  # oldest evicted
    assert answer_cache.get(keys[1]) == {"answer_text": "回答1"}
    assert answer_cache.get(keys[2]) == {"answer_text": "回答2"}


def test_approved_exact_match_precedes_and_is_uncached(monkeypatch, tmp_path):
    record = {
        "qa_id": "qa-cache-1",
        "question": "営業時間を教えてください",
        "approved_answer": "営業時間は9時から17時です。",
        "status": "approved",
        "tenant_id": "default",
        "approved_citations": [{"source_doc": "faq.pdf", "source_pages": [1]}],
    }
    approved_path = tmp_path / "approved.jsonl"
    approved_path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    chunks = tmp_path / "chunks.jsonl"
    chunks.write_text('{"id":"c1","text":"本文"}\n', encoding="utf-8")

    monkeypatch.setattr(config, "CHUNKS_JSONL_PATH", str(chunks))
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", True)
    monkeypatch.setattr(config, "APPROVED_QA_PATH", str(approved_path))
    monkeypatch.setattr(config, "ANSWER_CACHE_ENABLED", True)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(main, "_approved_qa_index", None)
    monkeypatch.setattr(main, "_approved_qa_index_path", None)
    answer_cache.clear()

    def forbidden(*args, **kwargs):
        raise AssertionError("pipeline must not run for approved exact match")

    monkeypatch.setattr(main, "answer_query_with_trace", forbidden)
    monkeypatch.setattr(main, "ensure_openai_client", forbidden)

    first = main.chat(main.ChatRequest(question="営業時間を教えてください"))
    second = main.chat(main.ChatRequest(question="営業時間を教えてください"))

    assert first["answer_mode"] == "approved_exact_match"
    assert second["answer_mode"] == "approved_exact_match"

    # Approved exact answers bypass the answer cache. Stable answer fields must
    # be identical, while per-request observability IDs must be newly issued.
    volatile_fields = {"request_id", "trace_id"}
    stable_first = {
        key: value for key, value in first.items()
        if key not in volatile_fields
    }
    stable_second = {
        key: value for key, value in second.items()
        if key not in volatile_fields
    }

    assert stable_second == stable_first
    assert second["request_id"] != first["request_id"]
    assert second["trace_id"] != first["trace_id"]
    assert answer_cache.size() == 0
