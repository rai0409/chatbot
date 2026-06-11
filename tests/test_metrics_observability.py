from __future__ import annotations

import json
from types import SimpleNamespace

import config
from rag_core import answer_cache, qa
from rag_core.retrieval import RetrievedChunk
from schemas import AnswerResult
from webapi import main, metrics_registry


def _mk_chunk(label: str, text: str) -> RetrievedChunk:
    return RetrievedChunk(
        text=text,
        metadata={
            "id": label,
            "source_doc": "doc.pdf",
            "source_pages": [1],
            "retrieval_source": "hybrid",
        },
        score=0.25,
    )


def _nonstream_client(content: str):
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )
    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: response))
    )


class _FakeStatusError(Exception):
    def __init__(self, status_code, message="provider error"):
        super().__init__(message)
        self.status_code = status_code


def test_registry_increment_snapshot_reset():
    metrics_registry.reset()
    metrics_registry.increment("chat_used_fallback_total")
    metrics_registry.increment("chat_answer_mode_total", "grounded")
    metrics_registry.increment("chat_answer_mode_total", "grounded")
    metrics_registry.increment("chat_answer_mode_total", "fallback")

    snap = metrics_registry.snapshot()
    assert snap["chat_used_fallback_total"] == {"total": 1}
    assert snap["chat_answer_mode_total"] == {"grounded": 2, "fallback": 1}

    metrics_registry.reset()
    assert metrics_registry.snapshot() == {}


def test_stage_latency_guard_path_has_retrieval_only(monkeypatch):
    monkeypatch.setattr(qa, "hybrid_retrieve", lambda *a, **k: [_mk_chunk("A", "一般説明です。")])
    monkeypatch.setattr(qa, "guard_merged_top", lambda *a, **k: "soft_distance")

    _, trace = qa.answer_query_with_trace("PR2 を教えて", client=object(), top_k=5)

    stage = trace["stage_latency_ms"]
    assert stage["retrieval_ms"] >= 0
    assert "generation_ms" not in stage


def test_stage_latency_grounded_path_has_generation(monkeypatch):
    monkeypatch.setattr(
        qa, "hybrid_retrieve", lambda *a, **k: [_mk_chunk("A", "PR2 は二段階確認の説明です。")]
    )
    monkeypatch.setattr(qa, "guard_merged_top", lambda *a, **k: None)
    client = _nonstream_client("- PR2 は二段階確認の説明です [S1]\n不足: なし [S1]")

    _, trace = qa.answer_query_with_trace("PR2 を教えて", client=client, top_k=5)

    stage = trace["stage_latency_ms"]
    assert stage["retrieval_ms"] >= 0
    assert stage["generation_ms"] >= 0


def _fake_pipeline(*, guard_reason=None, used_fallback=False, answer_mode=None):
    def fake(question, client=None, top_k=20, max_context_chars=8000, intent_override=None):
        ans = AnswerResult(
            answer_text="回答本文",
            answer_with_footnotes="回答本文",
            intent="other",
            guard_reason=guard_reason,
            used_fallback=used_fallback,
            citations=[],
            retrieved=[],
            rewritten_query="",
            augmented_query="",
        )
        trace = {
            "request_id": "req-1",
            "normalized_query": question,
            "intent": "other",
            "final_guard_reason": guard_reason,
            "final_used_fallback": used_fallback,
            "answer_mode": answer_mode,
            "citations_count": 0,
            "latency_ms": 1,
            "after_rerank": [],
        }
        return ans, trace

    return fake


def _setup_chat(monkeypatch, tmp_path, **pipeline_kwargs):
    chunks = tmp_path / "chunks.jsonl"
    chunks.write_text('{"id":"c1","text":"本文"}\n', encoding="utf-8")
    monkeypatch.setattr(config, "CHUNKS_JSONL_PATH", str(chunks))
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", False)
    monkeypatch.setattr(config, "ANSWER_CACHE_ENABLED", False)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(main, "ensure_openai_client", lambda base_url=None: object())
    monkeypatch.setattr(main, "answer_query_with_trace", _fake_pipeline(**pipeline_kwargs))
    answer_cache.clear()
    metrics_registry.reset()


def test_grounded_chat_increments_answer_mode(monkeypatch, tmp_path):
    _setup_chat(monkeypatch, tmp_path, answer_mode="grounded")

    main.chat(main.ChatRequest(question="質問です"))

    snap = metrics_registry.snapshot()
    assert snap["chat_answer_mode_total"] == {"grounded": 1}
    assert "chat_guard_reason_total" not in snap
    assert "chat_used_fallback_total" not in snap


def test_guard_fallback_chat_increments_guard_and_fallback(monkeypatch, tmp_path):
    _setup_chat(
        monkeypatch,
        tmp_path,
        guard_reason="soft_distance",
        used_fallback=True,
        answer_mode="fallback",
    )

    main.chat(main.ChatRequest(question="質問です"))

    snap = metrics_registry.snapshot()
    assert snap["chat_answer_mode_total"] == {"fallback": 1}
    assert snap["chat_guard_reason_total"] == {"soft_distance": 1}
    assert snap["chat_used_fallback_total"] == {"total": 1}


def test_approved_chat_increments_approved_mode(monkeypatch, tmp_path):
    record = {
        "qa_id": "qa-m-1",
        "question": "営業時間を教えてください",
        "approved_answer": "営業時間は9時から17時です。",
        "status": "approved",
        "tenant_id": "default",
        "approved_citations": [{"source_doc": "faq.pdf", "source_pages": [1]}],
    }
    path = tmp_path / "approved.jsonl"
    path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", True)
    monkeypatch.setattr(config, "APPROVED_QA_PATH", str(path))
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(main, "_approved_qa_index", None)
    monkeypatch.setattr(main, "_approved_qa_index_path", None)
    metrics_registry.reset()

    main.chat(main.ChatRequest(question="営業時間を教えてください"))

    snap = metrics_registry.snapshot()
    assert snap["chat_answer_mode_total"] == {"approved_exact_match": 1}


def test_cache_hit_increments_cache_counter(monkeypatch, tmp_path):
    _setup_chat(monkeypatch, tmp_path, answer_mode="grounded")
    monkeypatch.setattr(config, "ANSWER_CACHE_ENABLED", True)

    main.chat(main.ChatRequest(question="質問です"))
    main.chat(main.ChatRequest(question="質問です"))

    snap = metrics_registry.snapshot()
    assert snap["chat_cache_hit_total"] == {"total": 1}
    assert snap["chat_answer_mode_total"] == {"grounded": 2}


def test_provider_error_increments_error_counter(monkeypatch, tmp_path):
    _setup_chat(monkeypatch, tmp_path)

    def failing(*args, **kwargs):
        raise _FakeStatusError(429, "rate limit")

    monkeypatch.setattr(main, "answer_query_with_trace", failing)

    main.chat(main.ChatRequest(question="質問です"))

    snap = metrics_registry.snapshot()
    assert snap["chat_provider_error_total"] == {"rate_limited": 1}


def test_metrics_endpoint_includes_existing_fields_and_counters(monkeypatch, tmp_path):
    metrics_registry.reset()
    metrics_registry.increment("chat_answer_mode_total", "grounded")

    payload = main.metrics()

    assert set(payload.keys()) >= {
        "uptime_seconds",
        "total_requests",
        "error_requests",
        "counters",
    }
    assert payload["counters"]["chat_answer_mode_total"] == {"grounded": 1}
