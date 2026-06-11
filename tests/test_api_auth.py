from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from webapi import main
from webapi.api_auth import (
    api_auth_enabled,
    require_api_auth,
    require_api_auth_headers,
    require_search_debug_access,
    require_search_debug_access_headers,
    search_debug_enabled,
)


def _clear_auth_env(monkeypatch):
    monkeypatch.delenv("API_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("API_AUTH_KEYS", raising=False)
    monkeypatch.delenv("SEARCH_DEBUG_ENABLED", raising=False)
    monkeypatch.delenv("ADMIN_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("ADMIN_AUTH_TOKEN", raising=False)


def test_auth_disabled_by_default_allows_everything(monkeypatch):
    _clear_auth_env(monkeypatch)

    require_api_auth_headers({})
    require_api_auth_headers(None)
    require_search_debug_access_headers({})
    assert api_auth_enabled() is False
    assert search_debug_enabled() is True


def test_enabled_without_configured_keys_returns_503(monkeypatch):
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("API_AUTH_ENABLED", "true")

    with pytest.raises(HTTPException) as exc_info:
        require_api_auth_headers({"X-Api-Key": "anything"})

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "api auth is not configured"


def test_enabled_without_key_returns_401(monkeypatch):
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.setenv("API_AUTH_KEYS", "key-one,key-two")

    with pytest.raises(HTTPException) as exc_info:
        require_api_auth_headers({})

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "api authentication required"


def test_enabled_with_wrong_key_returns_403(monkeypatch):
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.setenv("API_AUTH_KEYS", "key-one,key-two")

    with pytest.raises(HTTPException) as exc_info:
        require_api_auth_headers({"X-Api-Key": "wrong-key"})

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "invalid api credentials"


def test_enabled_with_correct_key_both_header_forms(monkeypatch):
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.setenv("API_AUTH_KEYS", "key-one,key-two")

    require_api_auth_headers({"X-Api-Key": "key-one"})
    require_api_auth_headers({"Authorization": "Bearer key-two"})


def test_malformed_authorization_header_is_rejected(monkeypatch):
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.setenv("API_AUTH_KEYS", "key-one")

    with pytest.raises(HTTPException) as exc_info:
        require_api_auth_headers({"Authorization": "Token key-one"})

    assert exc_info.value.status_code == 403


def test_key_values_never_appear_in_error_details(monkeypatch):
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.setenv("API_AUTH_KEYS", "super-secret-key")

    with pytest.raises(HTTPException) as exc_info:
        require_api_auth_headers({"X-Api-Key": "provided-wrong-key"})

    detail = str(exc_info.value.detail)
    assert "super-secret-key" not in detail
    assert "provided-wrong-key" not in detail


def test_search_debug_disabled_returns_404(monkeypatch):
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("SEARCH_DEBUG_ENABLED", "false")

    with pytest.raises(HTTPException) as exc_info:
        require_search_debug_access_headers({})

    assert exc_info.value.status_code == 404


def test_search_debug_requires_admin_token_when_api_auth_enabled(monkeypatch):
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.setenv("API_AUTH_KEYS", "key-one")
    monkeypatch.setenv("ADMIN_AUTH_TOKEN", "admin-token")

    # API key alone is rejected.
    with pytest.raises(HTTPException) as exc_info:
        require_search_debug_access_headers({"X-Api-Key": "key-one"})
    assert exc_info.value.status_code == 401

    # API key plus admin token passes.
    require_search_debug_access_headers(
        {"X-Api-Key": "key-one", "X-Admin-Token": "admin-token"}
    )


def test_search_debug_fails_closed_when_admin_token_missing(monkeypatch):
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.setenv("API_AUTH_KEYS", "key-one")

    with pytest.raises(HTTPException) as exc_info:
        require_search_debug_access_headers({"X-Api-Key": "key-one"})

    assert exc_info.value.status_code == 503


def _route(path: str, method: str) -> APIRoute:
    for route in main.app.routes:
        if isinstance(route, APIRoute) and route.path == path and method in route.methods:
            return route
    raise AssertionError(f"route not found: {method} {path}")


def _has_dependency(route: APIRoute, func) -> bool:
    return any(dependency.call is func for dependency in route.dependant.dependencies)


def test_public_endpoints_are_guarded_by_api_auth():
    for path in ("/chat", "/search", "/chat/product-preview", "/chat/feedback"):
        assert _has_dependency(_route(path, "POST"), require_api_auth), path


def test_search_debug_route_uses_search_debug_access():
    assert _has_dependency(_route("/search/debug", "POST"), require_search_debug_access)


def test_health_route_is_unauthenticated():
    route = _route("/health", "GET")
    assert not _has_dependency(route, require_api_auth)
    assert not _has_dependency(route, require_search_debug_access)
