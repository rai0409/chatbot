from __future__ import annotations

import time
import warnings
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import config
from rag_core import answer_cache
from webapi import main, metrics_registry, oidc_auth

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from authlib.jose import JsonWebKey, JsonWebToken

_RS = JsonWebToken(["RS256"])
_ISSUER = "https://idp.example.com"
_CLIENT = "client-1"
_SESSION_SECRET = "test-session-secret-xyz"


@pytest.fixture()
def rsa_key():
    return JsonWebKey.generate_key("RSA", 2048, is_private=True)


def _jwks(key):
    return {"keys": [key.as_dict(is_private=False, kid="k1")]}


def _mint(key, **over):
    claims = {"iss": _ISSUER, "aud": _CLIENT, "sub": "user-123", "nonce": "N1",
              "exp": int(time.time()) + 300}
    claims.update(over)
    return _RS.encode({"alg": "RS256", "kid": "k1"}, claims, key).decode("ascii")


def _enable_oidc(monkeypatch, *, tenant_map=None):
    monkeypatch.setenv("ENTERPRISE_OIDC_ENABLED", "true")
    monkeypatch.setenv("OIDC_ISSUER", _ISSUER)
    monkeypatch.setenv("OIDC_CLIENT_ID", _CLIENT)
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "client-secret-xyz")
    monkeypatch.setenv("OIDC_REDIRECT_URI", "https://app.example/auth/oidc/callback")
    monkeypatch.setenv("OIDC_JWKS_URI", _ISSUER + "/jwks")
    monkeypatch.setenv("OIDC_SESSION_SECRET", _SESSION_SECRET)
    monkeypatch.setenv("OIDC_COOKIE_SECURE", "false")  # tests use http
    if tenant_map:
        monkeypatch.setenv("OIDC_TENANT_MAP", tenant_map)


def _clear(monkeypatch):
    for var in ("ENTERPRISE_OIDC_ENABLED", "OIDC_ISSUER", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET",
                "OIDC_REDIRECT_URI", "OIDC_JWKS_URI", "OIDC_SESSION_SECRET", "OIDC_COOKIE_SECURE",
                "OIDC_TENANT_MAP", "OIDC_TENANT_CLAIM", "API_AUTH_ENABLED", "API_AUTH_KEYS",
                "API_AUTH_TENANT_MAP", "ENTERPRISE_AUTH_ENABLED", "RATE_LIMIT_ENABLED"):
        monkeypatch.delenv(var, raising=False)


# --- token verification (pure) ---------------------------------------------


def test_verify_valid_token(monkeypatch, rsa_key):
    claims = oidc_auth.verify_id_token(_mint(rsa_key), jwks=_jwks(rsa_key), issuer=_ISSUER, audience=_CLIENT, nonce="N1")
    assert claims["sub"] == "user-123"


def test_verify_rejects_tamper_and_claims(monkeypatch, rsa_key):
    jwks = _jwks(rsa_key)
    for kwargs in (dict(nonce="WRONG"), dict(audience="other"), dict(issuer="https://evil")):
        with pytest.raises(Exception):
            oidc_auth.verify_id_token(
                _mint(rsa_key), jwks=jwks,
                issuer=kwargs.get("issuer", _ISSUER), audience=kwargs.get("audience", _CLIENT),
                nonce=kwargs.get("nonce", "N1"),
            )
    # expired
    with pytest.raises(Exception):
        oidc_auth.verify_id_token(_mint(rsa_key, exp=int(time.time()) - 5), jwks=jwks, issuer=_ISSUER, audience=_CLIENT, nonce="N1")
    # signed by a different key
    other = JsonWebKey.generate_key("RSA", 2048, is_private=True)
    with pytest.raises(Exception):
        oidc_auth.verify_id_token(_mint(other), jwks=jwks, issuer=_ISSUER, audience=_CLIENT, nonce="N1")


# --- default-off safety -----------------------------------------------------


def test_default_off_endpoints_404_and_session_ignored(monkeypatch):
    _clear(monkeypatch)
    client = TestClient(main.app)
    assert client.get("/auth/oidc/login").status_code == 404
    assert client.get("/auth/oidc/callback?code=x&state=y").status_code == 404
    # a forged session cookie is ignored when OIDC is disabled
    assert oidc_auth.resolve_oidc_session({"cookie": "kuraden_session=forged"}) is None


def test_api_key_path_unchanged_when_oidc_disabled(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.setenv("API_AUTH_KEYS", "key-a")
    monkeypatch.setenv("API_AUTH_TENANT_MAP", "key-a=tenant_a")
    from webapi.api_auth import require_api_auth_headers
    ctx = require_api_auth_headers({"X-Api-Key": "key-a"})
    assert ctx.authenticated is True and ctx.allowed_tenants == frozenset({"tenant_a"})


# --- login redirect ---------------------------------------------------------


def test_login_redirects_with_pkce_state(monkeypatch):
    _clear(monkeypatch)
    _enable_oidc(monkeypatch)
    client = TestClient(main.app)
    resp = client.get("/auth/oidc/login", follow_redirects=False)
    assert resp.status_code == 302
    loc = resp.headers["location"]
    assert "code_challenge=" in loc and "code_challenge_method=S256" in loc and "state=" in loc
    assert "set-cookie" in {k.lower() for k in resp.headers}
    # client secret never leaks into the redirect
    assert "client-secret-xyz" not in loc


# --- callback end-to-end (mock IdP) ----------------------------------------


def _drive_login_get_txn(client):
    resp = client.get("/auth/oidc/login", follow_redirects=False)
    loc = resp.headers["location"]
    import urllib.parse as up
    qs = dict(up.parse_qsl(up.urlparse(loc).query))
    # state and nonce are random per login; the IdP echoes the nonce into the
    # ID token, so the test mints tokens with the actual login nonce.
    return resp, qs["state"], qs["nonce"]


def test_callback_establishes_session_and_chat_works(monkeypatch, rsa_key):
    _clear(monkeypatch)
    _enable_oidc(monkeypatch, tenant_map="tenant_a=tenant_a")
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", False)
    monkeypatch.setattr(config, "ANSWER_CACHE_ENABLED", False)
    answer_cache.clear(); metrics_registry.reset()

    ans = SimpleNamespace(intent="faq", guard_reason=None, used_fallback=False, citations=[],
                          to_dict=lambda: {"answer_text": "ok", "citations": [], "retrieved": []})
    trace = {"request_id": "r", "normalized_query": "q", "intent": "faq", "final_guard_reason": None,
             "final_used_fallback": False, "citations_count": 0, "latency_ms": 1, "after_rerank": [], "answer_mode": "grounded"}
    monkeypatch.setattr(main, "ensure_openai_client", lambda base_url=None: object())
    pipeline = SimpleNamespace(calls=[])
    monkeypatch.setattr(main, "answer_query_with_trace", lambda *a, **k: (pipeline.calls.append(k) or (ans, trace)))
    monkeypatch.setattr(main, "_approved_qa_lookup", lambda *a, **k: None)

    monkeypatch.setattr(oidc_auth, "_fetch_jwks", lambda uri: _jwks(rsa_key))
    client = TestClient(main.app)
    _, state, nonce = _drive_login_get_txn(client)
    # mock the token endpoint to return an ID token carrying the login nonce.
    id_token = _mint(rsa_key, nonce=nonce, **{"tenant": "tenant_a"})
    monkeypatch.setattr(oidc_auth, "_exchange_code", lambda *a, **k: {"id_token": id_token})
    cb = client.get(f"/auth/oidc/callback?code=abc&state={state}", follow_redirects=False)
    assert cb.status_code == 302
    # the test client now holds the session cookie; an authorized chat works
    ok = client.post("/chat", json={"question": "q", "tenant_id": "tenant_a"})
    assert ok.status_code == 200
    assert pipeline.calls and pipeline.calls[0]["tenant_id"] == "tenant_a"
    # cross-tenant via the OIDC session is rejected
    pipeline.calls.clear()
    denied = client.post("/chat", json={"question": "q", "tenant_id": "tenant_b"})
    assert denied.status_code == 403
    assert pipeline.calls == []
    # no secret/token leaks
    blob = cb.text + ok.text + denied.text + str(dict(cb.headers))
    for forbidden in ("client-secret-xyz", _SESSION_SECRET, id_token, "sk-"):
        assert forbidden not in blob


def test_callback_state_mismatch_fails_closed(monkeypatch, rsa_key):
    _clear(monkeypatch)
    _enable_oidc(monkeypatch)
    monkeypatch.setattr(oidc_auth, "_exchange_code", lambda *a, **k: {"id_token": _mint(rsa_key)})
    monkeypatch.setattr(oidc_auth, "_fetch_jwks", lambda uri: _jwks(rsa_key))
    client = TestClient(main.app)
    _drive_login_get_txn(client)
    # state never matches the txn -> CSRF protection rejects before any exchange
    bad = client.get("/auth/oidc/callback?code=abc&state=WRONG-STATE", follow_redirects=False)
    assert bad.status_code == 400


def test_callback_unmapped_tenant_fails_closed(monkeypatch, rsa_key):
    _clear(monkeypatch)
    _enable_oidc(monkeypatch, tenant_map="tenant_a=tenant_a")
    monkeypatch.setattr(oidc_auth, "_fetch_jwks", lambda uri: _jwks(rsa_key))
    client = TestClient(main.app)
    _, state, nonce = _drive_login_get_txn(client)
    id_token = _mint(rsa_key, nonce=nonce, **{"tenant": "tenant_zzz"})
    monkeypatch.setattr(oidc_auth, "_exchange_code", lambda *a, **k: {"id_token": id_token})
    resp = client.get(f"/auth/oidc/callback?code=abc&state={state}", follow_redirects=False)
    assert resp.status_code == 403


def test_unconfigured_oidc_fails_closed(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ENTERPRISE_OIDC_ENABLED", "true")  # enabled but unconfigured
    client = TestClient(main.app)
    assert client.get("/auth/oidc/login", follow_redirects=False).status_code == 503


def test_session_round_trip_and_tamper(monkeypatch):
    _clear(monkeypatch)
    _enable_oidc(monkeypatch)
    session = oidc_auth._sign({"fp": "abc123", "authz": True, "tenants": "tenant_a"}, _SESSION_SECRET, 3600)
    ctx = oidc_auth.resolve_oidc_session({"cookie": f"{oidc_auth.SESSION_COOKIE}={session}"})
    assert ctx is not None and ctx.authenticated and ctx.allowed_tenants == frozenset({"tenant_a"})
    # tampered/forged cookie -> ignored
    assert oidc_auth.resolve_oidc_session({"cookie": f"{oidc_auth.SESSION_COOKIE}=not.a.jwt"}) is None
