from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse

import config
from rag_core import qa
from webapi import main


class _FakeStatusError(Exception):
    def __init__(self, status_code, message="provider error"):
        super().__init__(message)
        self.status_code = status_code


class _FakeConnectionError(Exception):
    pass


def _response(content="- 回答 [S1]"):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class _FakeClient:
    def __init__(self, outcomes):
        self.calls = []
        outcomes = list(outcomes)

        def create(**kwargs):
            self.calls.append(kwargs)
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def _no_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr(qa.time, "sleep", lambda seconds: sleeps.append(seconds))
    return sleeps


def test_create_passes_timeout_and_max_tokens(monkeypatch):
    monkeypatch.setattr(config, "CHAT_COMPLETION_TIMEOUT_SECONDS", 12.5)
    monkeypatch.setattr(config, "CHAT_COMPLETION_MAX_TOKENS", 256)
    client = _FakeClient([_response()])

    qa._create_chat_completion(client, [{"role": "user", "content": "質問"}])

    call = client.calls[0]
    assert call["timeout"] == 12.5
    assert call["max_tokens"] == 256
    assert call["model"] == config.CHAT_MODEL
    assert call["temperature"] == 0


def test_retry_on_retryable_error_succeeds_second_attempt(monkeypatch):
    monkeypatch.setattr(config, "CHAT_COMPLETION_MAX_RETRIES", 1)
    monkeypatch.setattr(config, "CHAT_COMPLETION_RETRY_BACKOFF_SECONDS", 0.5)
    sleeps = _no_sleep(monkeypatch)
    client = _FakeClient([_FakeStatusError(503), _response("- 成功 [S1]")])

    resp = qa._create_chat_completion(client, [{"role": "user", "content": "質問"}])

    assert resp.choices[0].message.content == "- 成功 [S1]"
    assert len(client.calls) == 2
    assert sleeps == [0.5]


def test_connection_and_timeout_errors_are_retryable(monkeypatch):
    monkeypatch.setattr(config, "CHAT_COMPLETION_MAX_RETRIES", 1)
    sleeps = _no_sleep(monkeypatch)
    client = _FakeClient([_FakeConnectionError("conn reset"), _response()])

    qa._create_chat_completion(client, [{"role": "user", "content": "質問"}])

    assert len(client.calls) == 2
    assert len(sleeps) == 1


def test_no_retry_on_non_retryable_error(monkeypatch):
    monkeypatch.setattr(config, "CHAT_COMPLETION_MAX_RETRIES", 3)
    sleeps = _no_sleep(monkeypatch)
    client = _FakeClient([_FakeStatusError(401, "auth failed")])

    with pytest.raises(_FakeStatusError):
        qa._create_chat_completion(client, [{"role": "user", "content": "質問"}])

    assert len(client.calls) == 1
    assert sleeps == []


def test_retries_are_bounded_with_doubling_backoff(monkeypatch):
    monkeypatch.setattr(config, "CHAT_COMPLETION_MAX_RETRIES", 2)
    monkeypatch.setattr(config, "CHAT_COMPLETION_RETRY_BACKOFF_SECONDS", 1.0)
    sleeps = _no_sleep(monkeypatch)
    client = _FakeClient(
        [_FakeStatusError(429), _FakeStatusError(429), _FakeStatusError(429)]
    )

    with pytest.raises(_FakeStatusError):
        qa._create_chat_completion(client, [{"role": "user", "content": "質問"}])

    assert len(client.calls) == 3
    assert sleeps == [1.0, 2.0]


def _chat_with_failing_pipeline(monkeypatch, tmp_path, exc):
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", False)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(main, "ensure_openai_client", lambda base_url=None: object())

    def failing(*args, **kwargs):
        raise exc

    monkeypatch.setattr(main, "answer_query_with_trace", failing)
    return main.chat(main.ChatRequest(question="質問です"))


def test_chat_returns_429_with_error_type_on_rate_limit(monkeypatch, tmp_path):
    result = _chat_with_failing_pipeline(
        monkeypatch, tmp_path, _FakeStatusError(429, "rate limit exceeded")
    )

    assert isinstance(result, JSONResponse)
    assert result.status_code == 429
    body = json.loads(result.body)
    assert body == {"detail": "chat generation unavailable", "error_type": "rate_limited"}


def test_chat_returns_503_on_provider_5xx(monkeypatch, tmp_path):
    result = _chat_with_failing_pipeline(monkeypatch, tmp_path, _FakeStatusError(502))

    assert isinstance(result, JSONResponse)
    assert result.status_code == 503
    body = json.loads(result.body)
    assert body == {"detail": "chat generation unavailable", "error_type": "provider_unavailable"}


def test_chat_returns_500_for_unrelated_internal_errors(monkeypatch, tmp_path):
    with pytest.raises(HTTPException) as exc_info:
        _chat_with_failing_pipeline(monkeypatch, tmp_path, ValueError("boom"))

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "internal error"
