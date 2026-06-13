from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.routing import APIRoute

import config
from rag_core import qa
from rag_core.retrieval import RetrievedChunk
from webapi import main
from webapi.api_auth import require_api_auth


class _FakeStatusError(Exception):
    def __init__(self, status_code, message="provider error"):
        super().__init__(message)
        self.status_code = status_code


def _mk_chunk(label: str, text: str, score: float = 0.25, page: int = 1) -> RetrievedChunk:
    return RetrievedChunk(
        text=text,
        metadata={
            "id": label,
            "source_doc": "doc.pdf",
            "source_pages": [page],
            "retrieval_source": "hybrid",
        },
        score=score,
    )


def _stream_chunks(*texts):
    return [
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])
        for text in texts
    ]


def _midstream_failure(chunks, exc):
    def gen():
        yield from chunks
        raise exc

    return gen()


class _FakeStreamClient:
    def __init__(self, outcomes):
        self.calls = []
        outcomes = list(outcomes)

        def create(**kwargs):
            self.calls.append(kwargs)
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return iter(outcome) if isinstance(outcome, list) else outcome

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def _forbidden_client():
    def _raise(**kwargs):
        raise AssertionError("LLM must not be called")

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_raise)))


def _nonstream_client(content: str):
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )
    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: response))
    )


def _collect_body(response):
    async def _drain():
        return [chunk async for chunk in response.body_iterator]

    return asyncio.run(_drain())


def _parse_sse(blocks):
    parsed = []
    for block in blocks:
        lines = [line for line in block.strip().splitlines() if line]
        assert lines[0].startswith("event: ") and lines[1].startswith("data: "), block
        parsed.append((lines[0][len("event: "):], json.loads(lines[1][len("data: "):])))
    return parsed


def test_guard_stream_emits_meta_then_final_without_deltas(monkeypatch):
    monkeypatch.setattr(qa, "hybrid_retrieve", lambda *a, **k: [_mk_chunk("A", "一般説明です。")])
    monkeypatch.setattr(qa, "guard_merged_top", lambda *a, **k: "soft_distance")

    events = list(qa.answer_query_stream("PR2 を教えて", client=_forbidden_client(), top_k=5))

    assert [name for name, _ in events] == ["meta", "final"]
    meta = events[0][1]
    assert meta["guard_reason"] == "soft_distance"
    assert meta["used_fallback"] is True
    result, trace = events[1][1]
    assert result.citations == []
    assert "[S" not in result.answer_text
    assert trace["answer_mode"] == "fallback"


def test_grounded_stream_deltas_concatenate_and_final_matches_nonstream(monkeypatch):
    hits = [_mk_chunk("A", "PR2 は二段階確認の説明です。")]
    monkeypatch.setattr(qa, "hybrid_retrieve", lambda *a, **k: list(hits))
    monkeypatch.setattr(qa, "guard_merged_top", lambda *a, **k: None)
    full = "- PR2 は二段階確認の説明です [S1]\n不足: なし [S1]"
    client = _FakeStreamClient(
        [_stream_chunks("- PR2 は", "二段階確認の説明です [S1]", "\n不足: なし [S1]")]
    )

    events = list(qa.answer_query_stream("PR2 を教えて", client=client, top_k=5))

    names = [name for name, _ in events]
    assert names == ["meta", "delta", "delta", "delta", "final"]
    streamed_text = "".join(payload["text"] for name, payload in events if name == "delta")
    assert streamed_text == full
    assert client.calls[0]["stream"] is True
    assert "max_tokens" in client.calls[0] and "timeout" in client.calls[0]

    stream_result, _ = events[-1][1]
    nonstream_result = qa.answer_query("PR2 を教えて", client=_nonstream_client(full), top_k=5)
    assert stream_result.to_dict() == nonstream_result.to_dict()
    assert stream_result.used_fallback is False
    assert len(stream_result.citations) == 1


def test_validation_failure_final_carries_extractive_fallback(monkeypatch):
    monkeypatch.setattr(
        qa, "hybrid_retrieve", lambda *a, **k: [_mk_chunk("A", "PR2 は二段階確認の説明です。")]
    )
    monkeypatch.setattr(qa, "guard_merged_top", lambda *a, **k: None)
    client = _FakeStreamClient([_stream_chunks("箇条書きではない", "不正な出力")])

    events = list(qa.answer_query_stream("PR2 を教えて", client=client, top_k=5))

    result, trace = events[-1][1]
    assert result.used_fallback is True
    assert len(result.citations) >= 1
    assert "参考資料:" in result.answer_with_footnotes
    assert trace["answer_mode"] == "fallback"


def test_midstream_error_aborts_without_final_or_retry(monkeypatch):
    monkeypatch.setattr(
        qa, "hybrid_retrieve", lambda *a, **k: [_mk_chunk("A", "PR2 は二段階確認の説明です。")]
    )
    monkeypatch.setattr(qa, "guard_merged_top", lambda *a, **k: None)
    monkeypatch.setattr(qa.time, "sleep", lambda s: None)
    client = _FakeStreamClient(
        [_midstream_failure(_stream_chunks("- 途中まで"), _FakeStatusError(503))]
    )

    events = []
    with pytest.raises(_FakeStatusError):
        for event in qa.answer_query_stream("PR2 を教えて", client=client, top_k=5):
            events.append(event)

    names = [name for name, _ in events]
    assert names == ["meta", "delta"]
    assert len(client.calls) == 1  # never retried after a token was emitted


def test_retry_before_first_token_then_stream(monkeypatch):
    monkeypatch.setattr(
        qa, "hybrid_retrieve", lambda *a, **k: [_mk_chunk("A", "PR2 は二段階確認の説明です。")]
    )
    monkeypatch.setattr(qa, "guard_merged_top", lambda *a, **k: None)
    monkeypatch.setattr(qa.time, "sleep", lambda s: None)
    monkeypatch.setattr(config, "CHAT_COMPLETION_MAX_RETRIES", 1)
    client = _FakeStreamClient(
        [
            _FakeStatusError(503),
            _stream_chunks("- PR2 は説明です [S1]", "\n不足: なし [S1]"),
        ]
    )

    events = list(qa.answer_query_stream("PR2 を教えて", client=client, top_k=5))

    assert len(client.calls) == 2
    assert [name for name, _ in events] == ["meta", "delta", "delta", "final"]


def test_chat_stream_endpoint_approved_single_event(monkeypatch, tmp_path):
    record = {
        "qa_id": "qa-stream-1",
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
    monkeypatch.setattr(main, "ensure_openai_client", _forbidden_client)

    response = main.chat_stream(main.ChatRequest(question="営業時間を教えてください"))

    assert response.media_type == "text/event-stream"
    events = _parse_sse(_collect_body(response))
    assert len(events) == 1
    name, payload = events[0]
    assert name == "approved"
    assert payload["answer_mode"] == "approved_exact_match"
    assert payload["answer_text"] == "営業時間は9時から17時です。"


def test_chat_stream_endpoint_error_before_first_token(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", False)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(qa, "hybrid_retrieve", lambda *a, **k: [_mk_chunk("A", "説明です。")])
    monkeypatch.setattr(qa, "guard_merged_top", lambda *a, **k: None)
    monkeypatch.setattr(qa.time, "sleep", lambda s: None)
    failing = _FakeStreamClient(
        [_FakeStatusError(429, "rate limit"), _FakeStatusError(429, "rate limit")]
    )
    monkeypatch.setattr(main, "ensure_openai_client", lambda base_url=None: failing)

    response = main.chat_stream(main.ChatRequest(question="PR2 を教えて"))

    events = _parse_sse(_collect_body(response))
    names = [name for name, _ in events]
    assert names == ["meta", "error"]
    assert events[-1][1] == {
        "detail": "chat generation unavailable",
        "error_type": "rate_limited",
    }
    assert len(failing.calls) == 2  # bounded retry happened before first token


def _route(path: str, method: str) -> APIRoute:
    for route in main.app.routes:
        if isinstance(route, APIRoute) and route.path == path and method in route.methods:
            return route
    raise AssertionError(f"route not found: {method} {path}")


def _has_api_auth(route: APIRoute) -> bool:
    # require_api_auth is wired in as a sub-dependency of the rate-limit
    # wrapper since prompt024, so traverse one level of sub-dependencies.
    for dep in route.dependant.dependencies:
        if dep.call is require_api_auth:
            return True
        if any(sub.call is require_api_auth for sub in dep.dependencies):
            return True
    return False


def test_chat_stream_route_has_api_auth_and_chat_unchanged():
    assert _has_api_auth(_route("/chat/stream", "POST"))
    assert _has_api_auth(_route("/chat", "POST"))
