from __future__ import annotations

import re
from types import SimpleNamespace

from fastapi.testclient import TestClient

import config
from rag_core import answer_cache
from webapi import main, metrics_registry


def _setup(monkeypatch, tmp_path, *, auth=False):
    for var in ("API_AUTH_ENABLED", "API_AUTH_KEYS", "API_AUTH_TENANT_MAP", "RATE_LIMIT_ENABLED"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", False)
    monkeypatch.setattr(config, "ANSWER_CACHE_ENABLED", False)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    if auth:
        monkeypatch.setenv("API_AUTH_ENABLED", "true")
        monkeypatch.setenv("API_AUTH_KEYS", "key-a,key-b")
        monkeypatch.setenv("API_AUTH_TENANT_MAP", "key-a=tenant_a,key-b=tenant_a|tenant_b")
    answer_cache.clear()
    metrics_registry.reset()
    stream = SimpleNamespace(calls=[])

    def fake_stream(*a, **k):
        stream.calls.append((a, k))
        return iter(())

    monkeypatch.setattr(main, "ensure_openai_client", lambda base_url=None: object())
    monkeypatch.setattr(main, "answer_query_stream", fake_stream)
    monkeypatch.setattr(main, "_approved_qa_lookup", lambda *a, **k: None)
    return stream


# --- workspace markup + wiring ---------------------------------------------


def test_chat_ui_serves_commercial_workspace(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    body = TestClient(main.app).get("/chat-ui").text
    assert "<html" in body.lower()
    # workspace structure: sidebar + citations panel + composer
    assert 'class="sidebar"' in body
    assert 'class="citations"' in body
    assert 'id="q"' in body and 'id="send"' in body
    assert 'id="newChat"' in body  # new-conversation control
    assert 'id="citations"' in body
    # wired to the existing endpoints only
    assert "/chat/stream" in body
    assert "/chat/feedback" in body


def test_no_hardcoded_key_in_workspace(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, auth=True)
    body = TestClient(main.app).get("/chat-ui").text
    assert "key-a" not in body and "key-b" not in body
    for pat in (r"sk-[A-Za-z0-9]{16,}", r"Bearer\s+[A-Za-z0-9]{16,}", r"AKIA[0-9A-Z]{16}"):
        assert re.search(pat, body) is None
    assert "X-Api-Key" in body
    assert not re.search(r'api[_-]?key\s*[:=]\s*["\'][A-Za-z0-9_\-]{8,}["\']', body, re.IGNORECASE)


# --- data endpoints stay protected; serving invokes no pipeline ------------


def test_data_endpoints_protected_with_auth(monkeypatch, tmp_path):
    stream = _setup(monkeypatch, tmp_path, auth=True)
    client = TestClient(main.app)
    assert client.post("/chat/stream", json={"question": "q", "tenant_id": "tenant_a"}).status_code == 401
    forbidden = client.post(
        "/chat/stream", json={"question": "q", "tenant_id": "tenant_b"}, headers={"X-Api-Key": "key-a"}
    )
    assert forbidden.status_code == 403
    assert stream.calls == []
    assert "key-a" not in forbidden.text


def test_serving_workspace_invokes_no_pipeline(monkeypatch, tmp_path):
    stream = _setup(monkeypatch, tmp_path)
    client = TestClient(main.app)
    client.get("/chat-ui")
    assert stream.calls == []
    assert metrics_registry.snapshot() == {}


def test_health_and_metrics_unaffected(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    client = TestClient(main.app)
    assert client.get("/health").status_code == 200
    assert client.get("/metrics").status_code == 200
