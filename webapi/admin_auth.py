from __future__ import annotations

import hmac
import os
from collections.abc import Mapping

from fastapi import HTTPException, Request


_ENABLED_VALUES = {"1", "true", "yes", "on"}


def admin_auth_enabled() -> bool:
    return str(os.getenv("ADMIN_AUTH_ENABLED", "")).strip().lower() in _ENABLED_VALUES


def _extract_token(headers: Mapping[str, str] | None) -> tuple[str | None, bool]:
    if not headers:
        return None, False

    admin_token = headers.get("x-admin-token") or headers.get("X-Admin-Token")
    if admin_token:
        return str(admin_token), False

    authorization = headers.get("authorization") or headers.get("Authorization")
    if not authorization:
        return None, False
    parts = str(authorization).strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        return None, True
    return parts[1].strip(), False


def require_admin_auth_headers(headers: Mapping[str, str] | None = None) -> None:
    if not admin_auth_enabled():
        return

    expected = str(os.getenv("ADMIN_AUTH_TOKEN", "")).strip()
    if not expected:
        raise HTTPException(status_code=503, detail="admin auth is not configured")

    provided, malformed = _extract_token(headers)
    if malformed:
        raise HTTPException(status_code=403, detail="invalid admin credentials")
    if not provided:
        raise HTTPException(status_code=401, detail="admin authentication required")
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="invalid admin credentials")


def require_admin_auth(request: Request) -> None:
    require_admin_auth_headers(request.headers)
