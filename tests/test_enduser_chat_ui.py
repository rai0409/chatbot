from __future__ import annotations

import re
from types import SimpleNamespace

from fastapi.testclient import TestClient

import config
from rag_core import answer_cache
from webapi import main, metrics_registry


class _Recorder:
    def __init__(self, result=None):
        self.calls = []
        self.result = result

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


def _fake_answer(question="質問です"):
    ans = SimpleNamespace(
        intent="faq",
        guard_reason=None,
        used_fallback=False,
        citations=[],
        to_dict=lambda: {"answer_text": "回答です", "citations": [], "retrieved": []},
    )
    trace = {
        "request_id": "req-1",
        "normalized_query": question,
        "intent": "faq",
        "final_guard_reason": None,
        "final_used_fallback": False,
        "citations_count": 0,
        "latency_ms": 1,
        "after_rerank": [],
        "answer_mode": "grounded",
    }
    return ans, trace


def _setup(monkeypatch, tmp_path, *, auth=False):
    monkeypatch.delenv("API_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("API_AUTH_KEYS", raising=False)
    monkeypatch.delenv("API_AUTH_TENANT_MAP", raising=False)
    monkeypatch.delenv("RATE_LIMIT_ENABLED", raising=False)
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", False)
    monkeypatch.setattr(config, "ANSWER_CACHE_ENABLED", False)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    if auth:
        monkeypatch.setenv("API_AUTH_ENABLED", "true")
        monkeypatch.setenv("API_AUTH_KEYS", "key-a,key-b")
        monkeypatch.setenv("API_AUTH_TENANT_MAP", "key-a=tenant_a,key-b=tenant_a|tenant_b")
    answer_cache.clear()
    metrics_registry.reset()
    pipeline = _Recorder(result=_fake_answer())
    stream_pipeline = _Recorder(result=iter(()))
    monkeypatch.setattr(main, "ensure_openai_client", lambda base_url=None: object())
    monkeypatch.setattr(main, "answer_query_with_trace", pipeline)
    monkeypatch.setattr(main, "answer_query_stream", stream_pipeline)
    monkeypatch.setattr(main, "_approved_qa_lookup", lambda *a, **k: None)
    return SimpleNamespace(pipeline=pipeline, stream_pipeline=stream_pipeline)


# --- UI serving -------------------------------------------------------------


def test_chat_ui_served_as_html(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    client = TestClient(main.app)

    resp = client.get("/chat-ui")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text
    # End-user chat UI markup is present and wired to the real endpoints.
    assert "<html" in body.lower()
    assert "/chat/stream" in body
    assert "/chat/feedback" in body
    assert 'id="q"' in body and 'id="send"' in body


def test_chat_ui_served_when_api_auth_enabled(monkeypatch, tmp_path):
    # The static shell serves regardless of API auth (data calls stay protected).
    _setup(monkeypatch, tmp_path, auth=True)
    client = TestClient(main.app)

    resp = client.get("/chat-ui")
    assert resp.status_code == 200
    assert "<html" in resp.text.lower()


# --- No secret / no hardcoded key ------------------------------------------


def test_chat_ui_html_has_no_hardcoded_api_key(monkeypatch, tmp_path):
    # Even with keys configured, the served page must not contain any key value.
    _setup(monkeypatch, tmp_path, auth=True)
    client = TestClient(main.app)

    body = client.get("/chat-ui").text

    # Configured pilot keys must never appear in the page source.
    assert "key-a" not in body
    assert "key-b" not in body
    # No common secret-like tokens baked into the page.
    for pat in (r"sk-[A-Za-z0-9]{16,}", r"Bearer\s+[A-Za-z0-9]{16,}", r"AKIA[0-9A-Z]{16}"):
        assert re.search(pat, body) is None
    # The page references the header NAME only (key supplied at runtime).
    assert "X-Api-Key" in body
    # No hardcoded assignment of an api key literal.
    assert not re.search(r'api[_-]?key\s*[:=]\s*["\'][A-Za-z0-9_\-]{8,}["\']', body, re.IGNORECASE)


# --- Data endpoints stay protected (UI does not bypass auth) ----------------


def test_data_endpoints_still_protected_with_auth(monkeypatch, tmp_path):
    fakes = _setup(monkeypatch, tmp_path, auth=True)
    client = TestClient(main.app)

    # No key -> 401, pipeline never runs.
    no_key = client.post("/chat/stream", json={"question": "質問です", "tenant_id": "tenant_a"})
    assert no_key.status_code == 401

    # Valid key, unauthorized tenant -> 403, pipeline never runs.
    forbidden = client.post(
        "/chat/stream",
        json={"question": "質問です", "tenant_id": "tenant_b"},
        headers={"X-Api-Key": "key-a"},
    )
    assert forbidden.status_code == 403
    assert fakes.stream_pipeline.calls == []
    # The UI route itself never leaks the key.
    assert "key-a" not in forbidden.text


def test_feedback_endpoint_still_protected_with_auth(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, auth=True)
    client = TestClient(main.app)

    # The UI uses a client-minted token as feedback_token; endpoint stays gated.
    no_key = client.post(
        "/chat/feedback",
        json={"feedback_token": "ui-token-1", "feedback_type": "good", "tenant_id": "tenant_a"},
    )
    assert no_key.status_code == 401


# --- UI feedback wiring uses the existing contract --------------------------


def test_feedback_contract_accepts_ui_token_shape(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)  # auth disabled
    monkeypatch.setattr(main, "append_feedback_audit_event", lambda event: True)
    client = TestClient(main.app)

    for ftype in ("good", "bad", "human_review_requested"):
        resp = client.post(
            "/chat/feedback",
            json={"feedback_token": "ui-uuid-xyz", "trace_id": "ui-uuid-xyz", "feedback_type": ftype},
        )
        assert resp.status_code == 200, ftype
        assert resp.json()["ok"] is True


# --- production_safe / answering behavior unchanged by the UI route ---------


def test_chat_ui_route_does_not_invoke_pipeline(monkeypatch, tmp_path):
    fakes = _setup(monkeypatch, tmp_path)
    client = TestClient(main.app)

    client.get("/chat-ui")

    # Serving the static page must not trigger retrieval/generation.
    assert fakes.pipeline.calls == []
    assert fakes.stream_pipeline.calls == []
    assert metrics_registry.snapshot() == {}


# --- /health and /metrics unaffected ---------------------------------------


def test_health_and_metrics_unaffected(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    client = TestClient(main.app)

    assert client.get("/health").status_code == 200
    assert client.get("/metrics").status_code == 200
