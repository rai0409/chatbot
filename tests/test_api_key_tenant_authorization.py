from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import config
from rag_core import answer_cache
from webapi import main, metrics_registry
from webapi.api_auth import (
    ApiAuthContext,
    enforce_tenant_authorization,
    require_api_auth_headers,
)


def _clear_auth_env(monkeypatch):
    monkeypatch.delenv("API_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("API_AUTH_KEYS", raising=False)
    monkeypatch.delenv("API_AUTH_TENANT_MAP", raising=False)
    monkeypatch.delenv("SEARCH_DEBUG_ENABLED", raising=False)
    monkeypatch.delenv("ADMIN_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("ADMIN_AUTH_TOKEN", raising=False)


# --- Auth-layer behavior -------------------------------------------------


def test_auth_disabled_ignores_tenant_map(monkeypatch):
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("API_AUTH_TENANT_MAP", "key-a=tenant_a")

    ctx = require_api_auth_headers({})

    assert ctx.authenticated is False
    assert ctx.tenant_authorization_enabled is False
    enforce_tenant_authorization(ctx, "tenant_a")
    enforce_tenant_authorization(ctx, "tenant_b")
    enforce_tenant_authorization(ctx, None)


def test_enabled_without_tenant_map_preserves_legacy_behavior(monkeypatch):
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.setenv("API_AUTH_KEYS", "key-a")

    ctx = require_api_auth_headers({"X-Api-Key": "key-a"})

    assert ctx.authenticated is True
    assert ctx.tenant_authorization_enabled is False
    enforce_tenant_authorization(ctx, "tenant_a")
    enforce_tenant_authorization(ctx, "tenant_b")


def _map_ctx(monkeypatch, key, tenant_map, keys="key-a,key-b,key-unmapped"):
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.setenv("API_AUTH_KEYS", keys)
    monkeypatch.setenv("API_AUTH_TENANT_MAP", tenant_map)
    return require_api_auth_headers({"X-Api-Key": key})


def test_key_mapped_to_single_tenant(monkeypatch):
    ctx = _map_ctx(monkeypatch, "key-a", "key-a=tenant_a,key-b=tenant_b")

    assert ctx.tenant_authorization_enabled is True
    enforce_tenant_authorization(ctx, "tenant_a")
    with pytest.raises(HTTPException) as exc_info:
        enforce_tenant_authorization(ctx, "tenant_b")
    assert exc_info.value.status_code == 403


def test_key_mapped_to_multiple_tenants(monkeypatch):
    ctx = _map_ctx(monkeypatch, "key-b", "key-a=tenant_a,key-b=tenant_a|tenant_b")

    enforce_tenant_authorization(ctx, "tenant_a")
    enforce_tenant_authorization(ctx, "tenant_b")
    with pytest.raises(HTTPException) as exc_info:
        enforce_tenant_authorization(ctx, "tenant_c")
    assert exc_info.value.status_code == 403


def test_unmapped_valid_key_fails_closed(monkeypatch):
    ctx = _map_ctx(monkeypatch, "key-unmapped", "key-a=tenant_a")

    assert ctx.authenticated is True
    assert ctx.allowed_tenants == frozenset()
    for tenant in ("tenant_a", "default", None):
        with pytest.raises(HTTPException) as exc_info:
            enforce_tenant_authorization(ctx, tenant)
        assert exc_info.value.status_code == 403


def test_blank_tenant_normalizes_to_default(monkeypatch):
    ctx = _map_ctx(monkeypatch, "key-a", "key-a=default")
    for tenant in (None, "", "   ", "default"):
        enforce_tenant_authorization(ctx, tenant)

    ctx = _map_ctx(monkeypatch, "key-a", "key-a=tenant_a")
    for tenant in (None, "", "   "):
        with pytest.raises(HTTPException) as exc_info:
            enforce_tenant_authorization(ctx, tenant)
        assert exc_info.value.status_code == 403


def test_empty_tenant_values_are_ignored_safely(monkeypatch):
    # "key-a=" and "key-b=|" carry no valid tenant -> both keys stay unmapped
    # and fail closed; the keyless "=tenant_x" entry grants nothing.
    ctx = _map_ctx(monkeypatch, "key-a", "key-a=,key-b=|,=tenant_x")

    assert ctx.tenant_authorization_enabled is True
    assert ctx.allowed_tenants == frozenset()
    with pytest.raises(HTTPException):
        enforce_tenant_authorization(ctx, "tenant_x")

    ctx = _map_ctx(monkeypatch, "key-b", "key-a=,key-b=|,=tenant_x")
    with pytest.raises(HTTPException):
        enforce_tenant_authorization(ctx, "default")


def test_duplicate_map_keys_merge_tenants(monkeypatch):
    ctx = _map_ctx(monkeypatch, "key-a", "key-a=tenant_a,key-a=tenant_b")

    enforce_tenant_authorization(ctx, "tenant_a")
    enforce_tenant_authorization(ctx, "tenant_b")


def test_context_never_carries_raw_key(monkeypatch):
    ctx = _map_ctx(monkeypatch, "key-a", "key-a=tenant_a")

    assert ctx.key_fingerprint
    assert "key-a" not in ctx.key_fingerprint
    with pytest.raises(HTTPException) as exc_info:
        enforce_tenant_authorization(ctx, "tenant_b")
    assert "key-a" not in str(exc_info.value.detail)


def test_direct_call_without_context_is_noop():
    # Direct endpoint-function calls in tests bypass dependency injection;
    # enforcement must not blow up on the Depends sentinel or None.
    enforce_tenant_authorization(None, "tenant_a")
    enforce_tenant_authorization(object(), "tenant_a")


# --- Endpoint enforcement -------------------------------------------------


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


def _setup_endpoint_env(monkeypatch, tmp_path, tenant_map="key-a=tenant_a,key-b=tenant_a|tenant_b"):
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.setenv("API_AUTH_KEYS", "key-a,key-b,key-unmapped")
    if tenant_map is not None:
        monkeypatch.setenv("API_AUTH_TENANT_MAP", tenant_map)

    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", False)
    monkeypatch.setattr(config, "ANSWER_CACHE_ENABLED", False)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    answer_cache.clear()
    metrics_registry.reset()

    pipeline = _Recorder(result=_fake_answer())
    stream_pipeline = _Recorder(result=iter(()))
    approved_lookup = _Recorder(result=None)
    monkeypatch.setattr(main, "ensure_openai_client", lambda base_url=None: object())
    monkeypatch.setattr(main, "answer_query_with_trace", pipeline)
    monkeypatch.setattr(main, "answer_query_stream", stream_pipeline)
    monkeypatch.setattr(main, "_approved_qa_lookup", approved_lookup)
    return SimpleNamespace(
        pipeline=pipeline,
        stream_pipeline=stream_pipeline,
        approved_lookup=approved_lookup,
    )


def test_chat_unauthorized_tenant_returns_403_without_pipeline(monkeypatch, tmp_path):
    fakes = _setup_endpoint_env(monkeypatch, tmp_path)
    client = TestClient(main.app)

    resp = client.post(
        "/chat",
        json={"question": "質問です", "tenant_id": "tenant_b"},
        headers={"X-Api-Key": "key-a"},
    )

    assert resp.status_code == 403
    assert fakes.approved_lookup.calls == []
    assert fakes.pipeline.calls == []
    assert "key-a" not in resp.text


def test_chat_authorized_tenant_succeeds(monkeypatch, tmp_path):
    fakes = _setup_endpoint_env(monkeypatch, tmp_path)
    client = TestClient(main.app)

    resp = client.post(
        "/chat",
        json={"question": "質問です", "tenant_id": "tenant_a"},
        headers={"X-Api-Key": "key-a"},
    )

    assert resp.status_code == 200
    assert resp.json()["answer_text"] == "回答です"
    assert len(fakes.pipeline.calls) == 1
    assert fakes.pipeline.calls[0][1]["tenant_id"] == "tenant_a"
    assert "key-a" not in resp.text


def test_chat_multi_tenant_key_can_access_both(monkeypatch, tmp_path):
    _setup_endpoint_env(monkeypatch, tmp_path)
    client = TestClient(main.app)

    for tenant in ("tenant_a", "tenant_b"):
        resp = client.post(
            "/chat",
            json={"question": "質問です", "tenant_id": tenant},
            headers={"X-Api-Key": "key-b"},
        )
        assert resp.status_code == 200, tenant


def test_chat_unmapped_key_fails_closed(monkeypatch, tmp_path):
    fakes = _setup_endpoint_env(monkeypatch, tmp_path)
    client = TestClient(main.app)

    resp = client.post(
        "/chat",
        json={"question": "質問です"},
        headers={"X-Api-Key": "key-unmapped"},
    )

    assert resp.status_code == 403
    assert fakes.pipeline.calls == []


def test_chat_without_tenant_map_preserves_legacy_endpoint_behavior(monkeypatch, tmp_path):
    _setup_endpoint_env(monkeypatch, tmp_path, tenant_map=None)
    client = TestClient(main.app)

    resp = client.post(
        "/chat",
        json={"question": "質問です", "tenant_id": "tenant_b"},
        headers={"X-Api-Key": "key-a"},
    )

    assert resp.status_code == 200


def test_chat_stream_unauthorized_tenant_403_before_streaming(monkeypatch, tmp_path):
    fakes = _setup_endpoint_env(monkeypatch, tmp_path)
    client = TestClient(main.app)

    resp = client.post(
        "/chat/stream",
        json={"question": "質問です", "tenant_id": "tenant_b"},
        headers={"X-Api-Key": "key-a"},
    )

    assert resp.status_code == 403
    assert "text/event-stream" not in resp.headers.get("content-type", "")
    assert fakes.stream_pipeline.calls == []
    assert fakes.approved_lookup.calls == []
    assert "key-a" not in resp.text


def test_chat_stream_authorized_tenant_streams(monkeypatch, tmp_path):
    fakes = _setup_endpoint_env(monkeypatch, tmp_path)
    ans, trace = _fake_answer()
    fakes.stream_pipeline.result = iter([("final", (ans, trace))])
    client = TestClient(main.app)

    resp = client.post(
        "/chat/stream",
        json={"question": "質問です", "tenant_id": "tenant_a"},
        headers={"X-Api-Key": "key-a"},
    )

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")
    assert "event: final" in resp.text
    assert len(fakes.stream_pipeline.calls) == 1


def test_product_preview_unauthorized_tenant_returns_403(monkeypatch, tmp_path):
    fakes = _setup_endpoint_env(monkeypatch, tmp_path)
    client = TestClient(main.app)

    resp = client.post(
        "/chat/product-preview",
        json={"query": "質問です", "tenant_id": "tenant_b"},
        headers={"X-Api-Key": "key-a"},
    )

    assert resp.status_code == 403
    assert fakes.approved_lookup.calls == []


def test_chat_feedback_enforces_tenant(monkeypatch, tmp_path):
    _setup_endpoint_env(monkeypatch, tmp_path)
    monkeypatch.setattr(main, "append_feedback_audit_event", lambda event: True)
    client = TestClient(main.app)

    denied = client.post(
        "/chat/feedback",
        json={"feedback_token": "tok-1", "feedback_type": "good", "tenant_id": "tenant_b"},
        headers={"X-Api-Key": "key-a"},
    )
    assert denied.status_code == 403

    allowed = client.post(
        "/chat/feedback",
        json={"feedback_token": "tok-1", "feedback_type": "good", "tenant_id": "tenant_a"},
        headers={"X-Api-Key": "key-a"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["ok"] is True
