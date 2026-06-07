from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from webapi import main
from webapi.admin_auth import require_admin_auth, require_admin_auth_headers


def test_auth_disabled_by_default_allows_admin_helper(monkeypatch):
    monkeypatch.delenv("ADMIN_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("ADMIN_AUTH_TOKEN", raising=False)

    require_admin_auth_headers({})


def test_admin_auth_enabled_false_allows_access(monkeypatch):
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "false")
    monkeypatch.delenv("ADMIN_AUTH_TOKEN", raising=False)

    require_admin_auth_headers({})


def test_enabled_auth_with_missing_token_denies_safely(monkeypatch):
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "true")
    monkeypatch.delenv("ADMIN_AUTH_TOKEN", raising=False)

    with pytest.raises(HTTPException) as exc_info:
        require_admin_auth_headers({})

    assert exc_info.value.status_code == 503
    assert "admin auth is not configured" == exc_info.value.detail


def test_enabled_auth_with_no_request_token_returns_401(monkeypatch):
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_AUTH_TOKEN", "secret-token")

    with pytest.raises(HTTPException) as exc_info:
        require_admin_auth_headers({})

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "admin authentication required"


def test_enabled_auth_with_wrong_x_admin_token_returns_403(monkeypatch):
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "yes")
    monkeypatch.setenv("ADMIN_AUTH_TOKEN", "secret-token")

    with pytest.raises(HTTPException) as exc_info:
        require_admin_auth_headers({"X-Admin-Token": "wrong-token"})

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "invalid admin credentials"


def test_enabled_auth_with_correct_x_admin_token_allows(monkeypatch):
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "on")
    monkeypatch.setenv("ADMIN_AUTH_TOKEN", "secret-token")

    require_admin_auth_headers({"X-Admin-Token": "secret-token"})


def test_enabled_auth_with_correct_bearer_token_allows(monkeypatch):
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "1")
    monkeypatch.setenv("ADMIN_AUTH_TOKEN", "secret-token")

    require_admin_auth_headers({"Authorization": "Bearer secret-token"})


def test_malformed_authorization_header_is_rejected(monkeypatch):
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_AUTH_TOKEN", "secret-token")

    with pytest.raises(HTTPException) as exc_info:
        require_admin_auth_headers({"Authorization": "Token secret-token"})

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "invalid admin credentials"


def test_expected_token_is_not_in_error_response(monkeypatch):
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_AUTH_TOKEN", "very-secret-token")

    with pytest.raises(HTTPException) as exc_info:
        require_admin_auth_headers({"X-Admin-Token": "wrong-token"})

    assert "very-secret-token" not in str(exc_info.value.detail)


def _route(path: str, method: str) -> APIRoute:
    for route in main.app.routes:
        if isinstance(route, APIRoute) and route.path == path and method in route.methods:
            return route
    raise AssertionError(f"route not found: {method} {path}")


def _has_admin_dependency(route: APIRoute) -> bool:
    return any(dependency.call is require_admin_auth for dependency in route.dependant.dependencies)


def test_admin_review_page_route_is_guarded():
    assert _has_admin_dependency(_route("/admin/review", "GET"))


def test_admin_review_items_route_is_guarded():
    assert _has_admin_dependency(_route("/admin/review/items", "GET"))


def test_admin_review_action_route_is_guarded():
    assert _has_admin_dependency(_route("/admin/review/action", "POST"))
