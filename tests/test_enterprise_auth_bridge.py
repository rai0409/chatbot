from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import config
from rag_core import answer_cache
from webapi import main, metrics_registry
from webapi.api_auth import enforce_tenant_authorization, require_api_auth_headers

TRUST = "proxy-trust-secret-xyz"


def _clear_env(monkeypatch):
    for var in (
        "API_AUTH_ENABLED", "API_AUTH_KEYS", "API_AUTH_TENANT_MAP",
        "ENTERPRISE_AUTH_ENABLED", "ENTERPRISE_AUTH_TRUST_TOKEN", "ENTERPRISE_AUTH_TENANT_MAP",
        "RATE_LIMIT_ENABLED",
    ):
        monkeypatch.delenv(var, raising=False)


def _enable_enterprise(monkeypatch, tenant_map="tenant_a=tenant_a,grpX=tenant_b|tenant_c"):
    monkeypatch.setenv("ENTERPRISE_AUTH_ENABLED", "true")
    monkeypatch.setenv("ENTERPRISE_AUTH_TRUST_TOKEN", TRUST)
    monkeypatch.setenv("ENTERPRISE_AUTH_TENANT_MAP", tenant_map)


# --- 1. default-off ignores spoofed enterprise headers ---------------------


def test_default_off_ignores_spoofed_headers(monkeypatch):
    _clear_env(monkeypatch)
    # API auth off + spoofed enterprise headers -> open context, headers ignored.
    ctx = require_api_auth_headers({
        "X-Enterprise-Tenant": "tenant_b",
        "X-Enterprise-Auth-Trust": "anything",
        "X-Enterprise-User": "attacker@example.com",
    })
    assert ctx.authenticated is False
    assert ctx.tenant_authorization_enabled is False
    # not restricted by the spoofed identity
    enforce_tenant_authorization(ctx, "tenant_b")


def test_default_off_api_key_path_unchanged_ignores_enterprise_headers(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.setenv("API_AUTH_KEYS", "key-a")
    monkeypatch.setenv("API_AUTH_TENANT_MAP", "key-a=tenant_a")
    # Valid key + spoofed enterprise headers: API key path wins; enterprise
    # identity does NOT broaden access.
    ctx = require_api_auth_headers({
        "X-Api-Key": "key-a",
        "X-Enterprise-Tenant": "tenant_b",
        "X-Enterprise-Auth-Trust": "anything",
    })
    assert ctx.authenticated is True
    assert ctx.allowed_tenants == frozenset({"tenant_a"})
    enforce_tenant_authorization(ctx, "tenant_a")
    with pytest.raises(HTTPException) as exc:
        enforce_tenant_authorization(ctx, "tenant_b")
    assert exc.value.status_code == 403


# --- 2. existing API key auth still works when enterprise enabled ----------


def test_api_key_path_works_when_enterprise_enabled(monkeypatch):
    _clear_env(monkeypatch)
    _enable_enterprise(monkeypatch)
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.setenv("API_AUTH_KEYS", "key-a")
    monkeypatch.setenv("API_AUTH_TENANT_MAP", "key-a=tenant_a")
    # No enterprise headers -> falls through to API key path unchanged.
    ctx = require_api_auth_headers({"X-Api-Key": "key-a"})
    assert ctx.authenticated is True
    assert ctx.allowed_tenants == frozenset({"tenant_a"})


# --- 3/4. enabled rejects missing / invalid trust signal -------------------


def test_enabled_rejects_missing_trust(monkeypatch):
    _clear_env(monkeypatch)
    _enable_enterprise(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        require_api_auth_headers({"X-Enterprise-Tenant": "tenant_a"})
    assert exc.value.status_code == 401


def test_enabled_rejects_invalid_trust(monkeypatch):
    _clear_env(monkeypatch)
    _enable_enterprise(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        require_api_auth_headers({
            "X-Enterprise-Tenant": "tenant_a", "X-Enterprise-Auth-Trust": "wrong-token",
        })
    assert exc.value.status_code == 403


def test_enabled_without_trust_token_configured_fails_closed(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("ENTERPRISE_AUTH_ENABLED", "true")
    monkeypatch.setenv("ENTERPRISE_AUTH_TENANT_MAP", "tenant_a=tenant_a")
    with pytest.raises(HTTPException) as exc:
        require_api_auth_headers({
            "X-Enterprise-Tenant": "tenant_a", "X-Enterprise-Auth-Trust": "anything",
        })
    assert exc.value.status_code == 503


# --- 5. enabled accepts valid trusted headers for matching tenant ----------


def test_enabled_accepts_valid_trusted_headers(monkeypatch):
    _clear_env(monkeypatch)
    _enable_enterprise(monkeypatch)
    ctx = require_api_auth_headers({
        "X-Enterprise-Tenant": "tenant_a",
        "X-Enterprise-Auth-Trust": TRUST,
        "X-Enterprise-User": "alice@example.com",
    })
    assert ctx.authenticated is True
    assert ctx.tenant_authorization_enabled is True
    assert ctx.allowed_tenants == frozenset({"tenant_a"})
    enforce_tenant_authorization(ctx, "tenant_a")
    # fingerprint must not be the raw identity
    assert ctx.key_fingerprint and "alice" not in ctx.key_fingerprint


def test_enabled_group_identity_maps_to_multiple_tenants(monkeypatch):
    _clear_env(monkeypatch)
    _enable_enterprise(monkeypatch)
    ctx = require_api_auth_headers({
        "X-Enterprise-Tenant": "grpX", "X-Enterprise-Auth-Trust": TRUST,
    })
    assert ctx.allowed_tenants == frozenset({"tenant_b", "tenant_c"})


# --- 6. enabled rejects cross-tenant + unmapped identity -------------------


def test_enabled_rejects_cross_tenant(monkeypatch):
    _clear_env(monkeypatch)
    _enable_enterprise(monkeypatch)
    ctx = require_api_auth_headers({
        "X-Enterprise-Tenant": "tenant_a", "X-Enterprise-Auth-Trust": TRUST,
    })
    with pytest.raises(HTTPException) as exc:
        enforce_tenant_authorization(ctx, "tenant_b")
    assert exc.value.status_code == 403


def test_enabled_rejects_unmapped_identity(monkeypatch):
    _clear_env(monkeypatch)
    _enable_enterprise(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        require_api_auth_headers({
            "X-Enterprise-Tenant": "unknown_tenant", "X-Enterprise-Auth-Trust": TRUST,
        })
    assert exc.value.status_code == 403


# --- 7. no secret/key/prompt/.env value in outputs (end-to-end) ------------


def _fake_answer():
    ans = SimpleNamespace(
        intent="faq", guard_reason=None, used_fallback=False, citations=[],
        to_dict=lambda: {"answer_text": "回答です", "citations": [], "retrieved": []},
    )
    trace = {
        "request_id": "r1", "normalized_query": "q", "intent": "faq",
        "final_guard_reason": None, "final_used_fallback": False,
        "citations_count": 0, "latency_ms": 1, "after_rerank": [], "answer_mode": "grounded",
    }
    return ans, trace


def _setup_chat(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", False)
    monkeypatch.setattr(config, "ANSWER_CACHE_ENABLED", False)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    answer_cache.clear()
    metrics_registry.reset()
    pipeline = SimpleNamespace(calls=[])

    def fake(*a, **k):
        pipeline.calls.append((a, k))
        return _fake_answer()

    monkeypatch.setattr(main, "ensure_openai_client", lambda base_url=None: object())
    monkeypatch.setattr(main, "answer_query_with_trace", fake)
    monkeypatch.setattr(main, "_approved_qa_lookup", lambda *a, **k: None)
    return pipeline


def test_endpoint_enterprise_auth_end_to_end_no_secret_exposure(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    _enable_enterprise(monkeypatch)
    pipeline = _setup_chat(monkeypatch, tmp_path)
    client = TestClient(main.app)

    ok = client.post(
        "/chat",
        json={"question": "質問です", "tenant_id": "tenant_a"},
        headers={"X-Enterprise-Tenant": "tenant_a", "X-Enterprise-Auth-Trust": TRUST,
                 "X-Enterprise-User": "alice@example.com"},
    )
    assert ok.status_code == 200
    assert pipeline.calls and pipeline.calls[0][1]["tenant_id"] == "tenant_a"

    # cross-tenant via enterprise identity is rejected before the pipeline.
    pipeline.calls.clear()
    denied = client.post(
        "/chat",
        json={"question": "質問です", "tenant_id": "tenant_b"},
        headers={"X-Enterprise-Tenant": "tenant_a", "X-Enterprise-Auth-Trust": TRUST},
    )
    assert denied.status_code == 403
    assert pipeline.calls == []

    blob = ok.text + denied.text + json.dumps(metrics_registry.snapshot())
    for forbidden in (TRUST, "alice@example.com", "sk-", "Bearer ", "OPENAI_API_KEY"):
        assert forbidden not in blob


def test_resolver_disabled_returns_none(monkeypatch):
    from webapi import enterprise_auth
    _clear_env(monkeypatch)
    assert enterprise_auth.resolve_enterprise_auth({"X-Enterprise-Tenant": "tenant_a"}) is None
    assert enterprise_auth.resolve_enterprise_auth(None) is None
