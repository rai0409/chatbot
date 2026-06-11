from __future__ import annotations

import hmac
import os
from collections.abc import Mapping

from fastapi import HTTPException, Request

from webapi.admin_auth import enforce_admin_token


_ENABLED_VALUES = {"1", "true", "yes", "on"}


def api_auth_enabled() -> bool:
    return str(os.getenv("API_AUTH_ENABLED", "")).strip().lower() in _ENABLED_VALUES


def search_debug_enabled() -> bool:
    raw = str(os.getenv("SEARCH_DEBUG_ENABLED", "")).strip().lower()
    if raw == "":
        return True
    return raw in _ENABLED_VALUES


def _configured_keys() -> list[str]:
    raw = str(os.getenv("API_AUTH_KEYS", ""))
    return [part.strip() for part in raw.split(",") if part.strip()]


def _extract_key(headers: Mapping[str, str] | None) -> tuple[str | None, bool]:
    if not headers:
        return None, False

    api_key = headers.get("x-api-key") or headers.get("X-Api-Key")
    if api_key:
        return str(api_key), False

    authorization = headers.get("authorization") or headers.get("Authorization")
    if not authorization:
        return None, False
    parts = str(authorization).strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        return None, True
    return parts[1].strip(), False


def require_api_auth_headers(headers: Mapping[str, str] | None = None) -> None:
    if not api_auth_enabled():
        return

    keys = _configured_keys()
    if not keys:
        raise HTTPException(status_code=503, detail="api auth is not configured")

    provided, malformed = _extract_key(headers)
    if malformed:
        raise HTTPException(status_code=403, detail="invalid api credentials")
    if not provided:
        raise HTTPException(status_code=401, detail="api authentication required")
    if not any(hmac.compare_digest(provided, key) for key in keys):
        raise HTTPException(status_code=403, detail="invalid api credentials")


def require_api_auth(request: Request) -> None:
    require_api_auth_headers(request.headers)


def require_search_debug_access_headers(headers: Mapping[str, str] | None = None) -> None:
    if not search_debug_enabled():
        raise HTTPException(status_code=404, detail="not found")
    require_api_auth_headers(headers)
    if api_auth_enabled():
        # /search/debug exposes retrieval internals: when the API is
        # auth-protected, an API key alone is not enough — the admin token
        # is enforced even if admin auth is not globally enabled.
        enforce_admin_token(headers)


def require_search_debug_access(request: Request) -> None:
    require_search_debug_access_headers(request.headers)
