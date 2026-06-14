from __future__ import annotations

import json
import time
import warnings

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import config
from rag_core import answer_cache
from webapi import main, metrics_registry, oidc_auth, rbac
from webapi.api_auth import ApiAuthContext, enforce_tenant_authorization

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from authlib.jose import JsonWebKey, JsonWebToken

_RS = JsonWebToken(["RS256"])
_ISSUER = "https://idp.example.com"
_CLIENT = "client-1"
_SS = "sess-secret"


# --- module: group -> tenant + role ----------------------------------------


def test_group_to_tenant_and_role():
    authz, allowed, role = rbac.resolve_role_and_tenants(
        ["eng", "admins"], group_tenant_map_raw="eng=tenant_a,sales=tenant_b",
        group_role_map_raw="admins=admin,eng=user",
    )
    assert authz is True
    assert allowed == frozenset({"tenant_a"})
    assert role == "admin"  # highest-privilege mapped group wins


def test_unmapped_group_fails_closed_and_least_privilege():
    authz, allowed, role = rbac.resolve_role_and_tenants(
        ["unknown"], group_tenant_map_raw="eng=tenant_a", group_role_map_raw="admins=admin",
    )
    assert authz is True and allowed == frozenset()  # caller will 403 downstream
    assert role == "viewer"  # no mapped role -> least privilege


def test_no_tenant_map_disables_authz():
    authz, allowed, role = rbac.resolve_role_and_tenants(["eng"], group_tenant_map_raw="", group_role_map_raw="")
    assert authz is False and allowed == frozenset() and role == "user"


def test_role_precedence_and_enforce_role():
    assert rbac.role_at_least("admin", "operator") is True
    assert rbac.role_at_least("user", "operator") is False
    # enforce_role gate
    admin_ctx = ApiAuthContext(authenticated=True, role="admin")
    rbac.enforce_role(admin_ctx, "operator")  # ok
    with pytest.raises(HTTPException) as exc:
        rbac.enforce_role(ApiAuthContext(authenticated=True, role="viewer"), "operator")
    assert exc.value.status_code == 403
    # non-context (Depends sentinel) is a no-op
    rbac.enforce_role(None, "admin")


def test_invalid_roles_ignored_in_map():
    m = rbac.parse_group_role_map("a=admin,b=superuser,c=viewer")
    assert m == {"a": "admin", "c": "viewer"}  # unknown role dropped


# --- OIDC session carries group-derived tenants + role ---------------------


@pytest.fixture()
def rsa_key():
    return JsonWebKey.generate_key("RSA", 2048, is_private=True)


def _jwks(key):
    return {"keys": [key.as_dict(is_private=False, kid="k1")]}


def _mint(key, **over):
    claims = {"iss": _ISSUER, "aud": _CLIENT, "sub": "user-1", "nonce": "N",
              "exp": int(time.time()) + 300}
    claims.update(over)
    return _RS.encode({"alg": "RS256", "kid": "k1"}, claims, key).decode("ascii")


def _enable(monkeypatch, **extra):
    for k, v in {
        "ENTERPRISE_OIDC_ENABLED": "true", "OIDC_ISSUER": _ISSUER, "OIDC_CLIENT_ID": _CLIENT,
        "OIDC_CLIENT_SECRET": "cs", "OIDC_REDIRECT_URI": "https://a/cb", "OIDC_JWKS_URI": _ISSUER + "/jwks",
        "OIDC_SESSION_SECRET": _SS, "OIDC_COOKIE_SECURE": "false",
    }.items():
        monkeypatch.setenv(k, v)
    for k, v in extra.items():
        monkeypatch.setenv(k, v)


def _login(client):
    import urllib.parse as up
    r = client.get("/auth/oidc/login", follow_redirects=False)
    qs = dict(up.parse_qsl(up.urlparse(r.headers["location"]).query))
    return qs["state"], qs["nonce"]


def test_oidc_session_group_mapping_end_to_end(monkeypatch, rsa_key):
    for v in ("API_AUTH_ENABLED", "OIDC_TENANT_MAP"):
        monkeypatch.delenv(v, raising=False)
    _enable(monkeypatch, OIDC_GROUP_TENANT_MAP="eng=tenant_a", OIDC_GROUP_ROLE_MAP="leads=operator")
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", False)
    monkeypatch.setattr(config, "ANSWER_CACHE_ENABLED", False)
    answer_cache.clear(); metrics_registry.reset()
    from types import SimpleNamespace
    ans = SimpleNamespace(intent="faq", guard_reason=None, used_fallback=False, citations=[],
                          to_dict=lambda: {"answer_text": "ok", "citations": [], "retrieved": []})
    trace = {"request_id": "r", "normalized_query": "q", "intent": "faq", "final_guard_reason": None,
             "final_used_fallback": False, "citations_count": 0, "latency_ms": 1, "after_rerank": [], "answer_mode": "grounded"}
    monkeypatch.setattr(main, "ensure_openai_client", lambda base_url=None: object())
    monkeypatch.setattr(main, "answer_query_with_trace", lambda *a, **k: (ans, trace))
    monkeypatch.setattr(main, "_approved_qa_lookup", lambda *a, **k: None)
    monkeypatch.setattr(oidc_auth, "_fetch_jwks", lambda uri: _jwks(rsa_key))

    client = TestClient(main.app)
    state, nonce = _login(client)
    id_token = _mint(rsa_key, nonce=nonce, groups=["eng", "leads"])
    monkeypatch.setattr(oidc_auth, "_exchange_code", lambda *a, **k: {"id_token": id_token})
    cb = client.get(f"/auth/oidc/callback?code=c&state={state}", follow_redirects=False)
    assert cb.status_code == 302
    # authorized tenant (from group eng) works
    assert client.post("/chat", json={"question": "q", "tenant_id": "tenant_a"}).status_code == 200
    # cross-tenant rejected
    assert client.post("/chat", json={"question": "q", "tenant_id": "tenant_b"}).status_code == 403
    # role surfaced via /ui/context if present (operator from group leads)
    ctx = client.get("/ui/context").json()
    assert ctx.get("role") in ("operator", "admin", "user")  # backend-authoritative


def test_oidc_unmapped_group_fails_closed(monkeypatch, rsa_key):
    for v in ("API_AUTH_ENABLED",):
        monkeypatch.delenv(v, raising=False)
    _enable(monkeypatch, OIDC_GROUP_TENANT_MAP="eng=tenant_a")
    monkeypatch.setattr(oidc_auth, "_fetch_jwks", lambda uri: _jwks(rsa_key))
    client = TestClient(main.app)
    state, nonce = _login(client)
    id_token = _mint(rsa_key, nonce=nonce, groups=["intruders"])
    monkeypatch.setattr(oidc_auth, "_exchange_code", lambda *a, **k: {"id_token": id_token})
    resp = client.get(f"/auth/oidc/callback?code=c&state={state}", follow_redirects=False)
    assert resp.status_code == 403


def test_audit_metrics_have_no_raw_group_or_identity(monkeypatch, rsa_key):
    monkeypatch.delenv("API_AUTH_ENABLED", raising=False)
    _enable(monkeypatch, OIDC_GROUP_TENANT_MAP="eng=tenant_a", OIDC_GROUP_ROLE_MAP="leads=operator")
    metrics_registry.reset()
    monkeypatch.setattr(oidc_auth, "_fetch_jwks", lambda uri: _jwks(rsa_key))
    client = TestClient(main.app)
    state, nonce = _login(client)
    id_token = _mint(rsa_key, nonce=nonce, sub="alice@example.com", groups=["eng", "leads"])
    monkeypatch.setattr(oidc_auth, "_exchange_code", lambda *a, **k: {"id_token": id_token})
    client.get(f"/auth/oidc/callback?code=c&state={state}", follow_redirects=False)
    snap = json.dumps(metrics_registry.snapshot())
    # metrics carry only enum role/accepted labels, never raw identity or groups
    assert "alice@example.com" not in snap
    assert "eng" not in snap and "leads" not in snap
    assert "api_role_total" in snap and "api_oidc_auth_total" in snap
