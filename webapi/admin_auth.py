from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from collections.abc import Mapping
from typing import FrozenSet, Optional, Tuple
from urllib.parse import urlsplit

from fastapi import HTTPException, Request


_ENABLED_VALUES = {"1", "true", "yes", "on"}

# Browser session for the admin pages (stage 9). Browsers cannot attach
# X-Admin-Token to a page navigation, so /admin/login exchanges the token for
# this signed cookie; enforce_admin_token accepts either.
#
# Stage 10:
# - the cookie carries a session id and its issue time; logout revokes that
#   session on the server and /admin/logout-all every admin browser session
#   (shared through state_backend, so all nodes agree)
# - CSRF: a request authenticated by the cookie that changes state (POST,
#   PUT, PATCH, DELETE) must send X-CSRF-Token (the value of the admin_csrf
#   cookie, readable by the admin pages' script) and, when the browser sends
#   Origin, come from the same host
ADMIN_SESSION_COOKIE = "admin_session"
ADMIN_CSRF_COOKIE = "admin_csrf"
CSRF_HEADER = "x-csrf-token"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_REVOCATION_IDENTITY = "admin-browser"


def admin_auth_enabled() -> bool:
    return str(os.getenv("ADMIN_AUTH_ENABLED", "")).strip().lower() in _ENABLED_VALUES


def insecure_admin_access_allowed() -> bool:
    # Local development only: lets admin routes run without auth. Never set
    # this in a customer deployment.
    return str(os.getenv("ADMIN_AUTH_ALLOW_INSECURE_DEV", "")).strip().lower() in _ENABLED_VALUES


def admin_session_ttl_seconds() -> int:
    try:
        return max(60, int(os.getenv("ADMIN_SESSION_TTL_SECONDS", "") or 28800))
    except ValueError:
        return 28800


def _expected_token() -> str:
    return str(os.getenv("ADMIN_AUTH_TOKEN", "")).strip()


# Stage 15: tenant-scoped administrators. ADMIN_AUTH_TOKEN is the operator of
# the whole installation (every tenant); ADMIN_AUTH_TENANT_TOKENS gives a
# customer's own administrator a token limited to its tenants:
#   ADMIN_AUTH_TENANT_TOKENS=token1=tenant_a|tenant_b,token2=tenant_c
# Sessions are still signed with ADMIN_AUTH_TOKEN (it must be set). None as a
# scope means every tenant.
AdminScope = Optional[FrozenSet[str]]


def _tenant_tokens() -> dict:
    out: dict = {}
    for entry in str(os.getenv("ADMIN_AUTH_TENANT_TOKENS", "")).split(","):
        token, _, tenants = entry.strip().partition("=")
        scope = frozenset(t.strip() for t in tenants.split("|") if t.strip())
        if token.strip() and scope:
            out[token.strip()] = out.get(token.strip(), frozenset()) | scope
    return out


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def token_scope(provided: str) -> Tuple[bool, AdminScope]:
    """(valid, scope) of a presented admin token."""
    expected = _expected_token()
    if expected and _same(provided, expected):
        return True, None
    scope: FrozenSet[str] = frozenset()
    for token, tenants in _tenant_tokens().items():
        if _same(provided, token):
            scope = scope | tenants
    return (True, scope) if scope else (False, frozenset())


def _encode_scope(scope: AdminScope) -> str:
    if scope is None:
        return "*"
    raw = "\n".join(sorted(scope)).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") or "-"


def _decode_scope(value: str) -> AdminScope:
    if value == "*":
        return None
    if value == "-":
        return frozenset()
    padded = value + "=" * (-len(value) % 4)
    return frozenset(t for t in base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8").split("\n") if t)


def _mac(expected_token: str, message: str) -> str:
    # Keyed on the admin token: rotating ADMIN_AUTH_TOKEN ends every session.
    return hmac.new(expected_token.encode("utf-8"), message.encode("ascii"), hashlib.sha256).hexdigest()


def _session_signature(expected_token: str, sid: str, issued_ms: int, expires_at: int, scope: str = "*") -> str:
    # v3: the issue time is in milliseconds, so "end every session" spares a
    # session issued right after it in the same second. v4 (stage 15) also
    # signs the tenant scope; a v3 cookie is a whole-installation session.
    if scope == "*":
        return _mac(expected_token, f"admin-session:v3:{sid}:{issued_ms}:{expires_at}")
    return _mac(expected_token, f"admin-session:v4:{sid}:{issued_ms}:{expires_at}:{scope}")


def issue_admin_session(now: float | None = None, scope: AdminScope = None) -> tuple[str, int]:
    """(cookie value, lifetime in seconds) for a caller that presented a token."""
    expected = _expected_token()
    if not expected:
        raise HTTPException(status_code=503, detail="admin auth is not configured")
    ttl = admin_session_ttl_seconds()
    issued = time.time() if now is None else float(now)
    issued_ms = int(issued * 1000)
    expires_at = int(issued) + ttl
    sid = secrets.token_hex(12)
    encoded = _encode_scope(scope)
    signature = _session_signature(expected, sid, issued_ms, expires_at, encoded)
    if encoded == "*":
        return f"{sid}.{issued_ms}.{expires_at}.{signature}", ttl
    return f"{sid}.{issued_ms}.{expires_at}.{encoded}.{signature}", ttl


def parse_admin_session(cookie: str | None) -> Optional[Tuple[str, int, int, str]]:
    """(sid, issued_ms, expires_at, signature) or None when malformed."""
    parsed = _parse_session(cookie)
    return None if parsed is None else parsed[:4]


def _parse_session(cookie: str | None) -> Optional[Tuple[str, int, int, str, str]]:
    """(sid, issued_ms, expires_at, signature, encoded scope)."""
    parts = str(cookie or "").split(".")
    if len(parts) == 4:
        sid, issued_raw, expires_raw, signature = parts
        scope = "*"
    elif len(parts) == 5:
        sid, issued_raw, expires_raw, scope, signature = parts
    else:
        return None
    try:
        return sid, int(issued_raw), int(expires_raw), signature, scope
    except ValueError:
        return None


def csrf_token_for(cookie: str) -> str:
    parsed = parse_admin_session(cookie)
    expected = _expected_token()
    if parsed is None or not expected:
        return ""
    return _mac(expected, f"admin-csrf:{parsed[0]}")


def _cookie(headers: Mapping[str, str] | None, name: str) -> str | None:
    if not headers:
        return None
    raw = headers.get("cookie") or headers.get("Cookie")
    if not raw:
        return None
    for part in str(raw).split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value.strip()
    return None


def _session_cookie(headers: Mapping[str, str] | None) -> str | None:
    return _cookie(headers, ADMIN_SESSION_COOKIE)


def _valid_admin_session(cookie: str | None, expected: str, now: float | None = None) -> bool:
    parsed = _parse_session(cookie)
    if parsed is None:
        return False
    sid, issued_ms, expires_at, signature, scope = parsed
    current = int(time.time() if now is None else now)
    if expires_at < current or expires_at - issued_ms // 1000 > admin_session_ttl_seconds():
        return False
    if not hmac.compare_digest(signature, _session_signature(expected, sid, issued_ms, expires_at, scope)):
        return False
    from webapi import state_backend

    return not state_backend.is_session_revoked(sid, _REVOCATION_IDENTITY, issued_ms / 1000.0)


def revoke_admin_session(cookie: str | None) -> bool:
    """Logout: this browser session stops working on every node."""
    parsed = parse_admin_session(cookie)
    if parsed is None:
        return False
    from webapi import state_backend

    state_backend.revoke_session(parsed[0], float(parsed[2]))
    return True


def revoke_all_admin_sessions() -> None:
    """Every admin browser session issued until now is rejected."""
    from webapi import state_backend

    state_backend.revoke_user_sessions(_REVOCATION_IDENTITY, keep_seconds=admin_session_ttl_seconds() + 60)


def _header(headers: Mapping[str, str] | None, name: str) -> str:
    if not headers:
        return ""
    return str(headers.get(name) or headers.get(name.title()) or headers.get(name.upper()) or "")


def _enforce_csrf(headers: Mapping[str, str] | None, cookie: str) -> None:
    origin = _header(headers, "origin")
    host = _header(headers, "host")
    if origin and host and urlsplit(origin).netloc.lower() != host.lower():
        raise HTTPException(status_code=403, detail="cross-origin admin request refused")
    provided = _header(headers, CSRF_HEADER) or _header(headers, "X-CSRF-Token")
    expected = csrf_token_for(cookie)
    if not provided or not expected or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="csrf token missing or invalid")


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


def enforce_admin_token(headers: Mapping[str, str] | None = None, method: str = "GET") -> None:
    expected = _expected_token()
    if not expected:
        raise HTTPException(status_code=503, detail="admin auth is not configured")

    provided, malformed = _extract_token(headers)
    if malformed:
        raise HTTPException(status_code=403, detail="invalid admin credentials")
    if not provided:
        cookie = _session_cookie(headers)
        if cookie and _valid_admin_session(cookie, expected):
            if str(method or "GET").upper() not in _SAFE_METHODS:
                _enforce_csrf(headers, cookie)
            return
        raise HTTPException(status_code=401, detail="admin authentication required")
    if not token_scope(provided)[0]:
        raise HTTPException(status_code=403, detail="invalid admin credentials")


def admin_scope(headers: Mapping[str, str] | None) -> AdminScope:
    """Tenants this (already authenticated) admin request may act on; None = all.

    From the presented token or the session cookie, narrowed by the OIDC
    session's tenants when the request carries one."""
    if not admin_auth_enabled():
        return None  # development override (insecure_admin_access_allowed)
    provided, _ = _extract_token(headers)
    if provided:
        scope = token_scope(provided)[1]
    else:
        parsed = _parse_session(_session_cookie(headers))
        scope = _decode_scope(parsed[4]) if parsed else frozenset()
    from webapi import oidc_auth

    session = oidc_auth.resolve_oidc_session(headers)
    if session is not None and session.tenant_authorization_enabled:
        allowed = frozenset(session.allowed_tenants)
        scope = allowed if scope is None else scope & allowed
    return scope


def require_scope(scope: AdminScope, tenant: Optional[str]) -> None:
    """403 unless the tenant is inside the admin's scope."""
    if scope is None:
        return
    if (str(tenant or "").strip() or "default") not in scope:
        raise HTTPException(status_code=403, detail="tenant is outside this administrator's scope")


def require_global(scope: AdminScope) -> None:
    """Installation-wide actions (all sessions, global synonyms): ADMIN_AUTH_TOKEN only."""
    if scope is not None:
        raise HTTPException(status_code=403, detail="this action needs the installation administrator")


def _enforce_session_role(headers: Mapping[str, str] | None) -> None:
    # A shared admin token must not let a low-privilege SSO session act as an
    # operator: when the request also carries an OIDC session, its role must be
    # operator or admin. Lazy imports avoid an import cycle (rbac -> api_auth
    # -> admin_auth).
    from webapi import oidc_auth, rbac

    session = oidc_auth.resolve_oidc_session(headers)
    if session is None or not session.role:
        return
    rbac.enforce_role(session, rbac.ROLE_OPERATOR)


def require_admin_auth_headers(headers: Mapping[str, str] | None = None, method: str = "GET") -> None:
    # Fail closed: with admin auth not enabled, admin routes are refused unless
    # the explicit development override is set.
    if not admin_auth_enabled():
        if insecure_admin_access_allowed():
            return
        raise HTTPException(status_code=403, detail="admin auth is not configured")
    enforce_admin_token(headers, method=method)
    _enforce_session_role(headers)


def require_admin_auth(request: Request) -> None:
    require_admin_auth_headers(request.headers, method=request.method)
