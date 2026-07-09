from __future__ import annotations

import json

from fastapi.testclient import TestClient

from webapi import main


def _clear(monkeypatch):
    for var in ("ADMIN_AUTH_ENABLED", "ADMIN_AUTH_TOKEN", "API_AUTH_ENABLED",
                "BRANDING_PRODUCT_NAME", "BRANDING_SUBTITLE", "BRANDING_THEME_COLOR", "BRANDING_LOGO_TEXT"):
        monkeypatch.delenv(var, raising=False)


# --- branding (safe, no secrets) -------------------------------------------


def test_branding_defaults_no_secret(monkeypatch):
    _clear(monkeypatch)
    body = TestClient(main.app).get("/branding").json()
    assert set(body) == {"product_name", "subtitle", "theme_color", "logo_text"}
    blob = json.dumps(body)
    for forbidden in ("sk-", "Bearer ", "X-Api-Key", "ADMIN_AUTH_TOKEN", "API_AUTH_KEYS"):
        assert forbidden not in blob


def test_branding_overridable_via_env(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("BRANDING_PRODUCT_NAME", "架空精機ナレッジ")
    body = TestClient(main.app).get("/branding").json()
    assert body["product_name"] == "架空精機ナレッジ"


# --- role context is backend-authoritative ---------------------------------


def test_ui_context_role_open_mode(monkeypatch):
    _clear(monkeypatch)  # admin auth disabled -> open/dev -> admin role
    ctx = TestClient(main.app).get("/ui/context").json()
    assert ctx["role"] == "admin"
    assert ctx["admin_auth_enabled"] is False


def test_ui_context_role_user_without_token(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_AUTH_TOKEN", "admin-secret")
    ctx = TestClient(main.app).get("/ui/context").json()
    assert ctx["role"] == "user"  # no token -> not admin
    # admin role only with a valid token
    ctx2 = TestClient(main.app).get("/ui/context", headers={"X-Admin-Token": "admin-secret"}).json()
    assert ctx2["role"] == "admin"


def test_ui_context_exposes_no_secret(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_AUTH_TOKEN", "admin-secret")
    ctx = TestClient(main.app).get("/ui/context", headers={"X-Admin-Token": "admin-secret"}).json()
    assert "admin-secret" not in json.dumps(ctx)


# --- backend enforcement is authoritative (frontend cannot bypass) ---------


def test_admin_routes_enforced_server_side(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_AUTH_TOKEN", "admin-secret")
    client = TestClient(main.app)
    # no token -> 401; wrong token -> 403; regardless of any UI role claim
    assert client.get("/admin/review/items").status_code == 401
    assert client.get("/admin/review/items", headers={"X-Admin-Token": "nope"}).status_code == 403
    ok = client.get("/admin/review/items", headers={"X-Admin-Token": "admin-secret"})
    assert ok.status_code == 200


def test_admin_action_enforced_server_side(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_AUTH_TOKEN", "admin-secret")
    client = TestClient(main.app)
    r = client.post("/admin/review/action", json={"review_id": "x", "action_type": "approve_candidate"})
    assert r.status_code in (401, 403)


# --- workspace serves role/branding hooks (cosmetic) -----------------------


def test_workspace_has_admin_link_and_branding_hooks(monkeypatch):
    _clear(monkeypatch)
    body = TestClient(main.app).get("/chat-ui").text
    assert 'id="adminLink"' in body
    assert "/branding" in body and "/ui/context" in body
    assert 'id="brandName"' in body
