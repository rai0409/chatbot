from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from fastapi import HTTPException, Request

from webapi import metrics_registry
from webapi.admin_auth import enforce_admin_token


def _record_auth_rejection(reason: str) -> None:
    # Observability only: stable enum labels, never raw keys or query text.
    metrics_registry.increment("api_auth_rejection_total", reason)


_ENABLED_VALUES = {"1", "true", "yes", "on"}

# Mirrors rag_core.retrieval.normalize_tenant_id (missing/blank -> "default")
# without importing the retrieval stack into the auth layer.
_DEFAULT_TENANT_ID = "default"


def _normalize_tenant(value) -> str:
    text = str(value or "").strip()
    return text or _DEFAULT_TENANT_ID


@dataclass(frozen=True)
class ApiAuthContext:
    authenticated: bool = False
    tenant_authorization_enabled: bool = False
    allowed_tenants: frozenset[str] = field(default_factory=frozenset)
    # sha256-derived identifier for audit purposes; never the raw key.
    key_fingerprint: str | None = None

    def tenant_allowed(self, tenant_id) -> bool:
        if not self.tenant_authorization_enabled:
            return True
        return _normalize_tenant(tenant_id) in self.allowed_tenants


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


def _tenant_map_raw() -> str:
    return str(os.getenv("API_AUTH_TENANT_MAP", "")).strip()


def _parse_tenant_map(raw: str) -> dict[str, frozenset[str]]:
    # Format: key1=tenant_a,key2=tenant_b,key3=tenant_a|tenant_b
    # Entries without a key or without at least one non-blank tenant are
    # ignored (misconfigured entries must not grant access). Duplicate keys
    # merge their tenant sets.
    mapping: dict[str, frozenset[str]] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry or "=" not in entry:
            continue
        key, _, tenants_raw = entry.partition("=")
        key = key.strip()
        tenants = frozenset(
            _normalize_tenant(part) for part in tenants_raw.split("|") if part.strip()
        )
        if not key or not tenants:
            continue
        mapping[key] = mapping.get(key, frozenset()) | tenants
    return mapping


def _key_fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def _allowed_tenants_for_key(provided: str, tenant_map: dict[str, frozenset[str]]) -> frozenset[str]:
    allowed: frozenset[str] = frozenset()
    for map_key, tenants in tenant_map.items():
        if hmac.compare_digest(provided, map_key):
            allowed = allowed | tenants
    return allowed


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


def require_api_auth_headers(headers: Mapping[str, str] | None = None) -> ApiAuthContext:
    # Optional default-off enterprise auth bridge (trusted reverse-proxy
    # headers). Lazy import avoids a circular import; returns None when disabled
    # or when the request carries no enterprise signal, so the API key path
    # below stays exactly unchanged.
    from webapi import enterprise_auth

    enterprise_ctx = enterprise_auth.resolve_enterprise_auth(headers)
    if enterprise_ctx is not None:
        return enterprise_ctx

    if not api_auth_enabled():
        return ApiAuthContext()

    keys = _configured_keys()
    if not keys:
        raise HTTPException(status_code=503, detail="api auth is not configured")

    provided, malformed = _extract_key(headers)
    if malformed:
        _record_auth_rejection("invalid_credentials")
        raise HTTPException(status_code=403, detail="invalid api credentials")
    if not provided:
        _record_auth_rejection("missing_credentials")
        raise HTTPException(status_code=401, detail="api authentication required")
    if not any(hmac.compare_digest(provided, key) for key in keys):
        _record_auth_rejection("invalid_credentials")
        raise HTTPException(status_code=403, detail="invalid api credentials")

    raw_map = _tenant_map_raw()
    if not raw_map:
        # Backward compatible: with no tenant map configured, any valid API
        # key may access any requested tenant_id.
        return ApiAuthContext(
            authenticated=True,
            tenant_authorization_enabled=False,
            key_fingerprint=_key_fingerprint(provided),
        )

    # A non-empty map activates strict enforcement. Keys absent from the map
    # (including all-invalid map entries) get an empty tenant set and fail
    # closed with 403 at enforcement time.
    tenant_map = _parse_tenant_map(raw_map)
    return ApiAuthContext(
        authenticated=True,
        tenant_authorization_enabled=True,
        allowed_tenants=_allowed_tenants_for_key(provided, tenant_map),
        key_fingerprint=_key_fingerprint(provided),
    )


def require_api_auth(request: Request) -> ApiAuthContext:
    return require_api_auth_headers(request.headers)


def enforce_tenant_authorization(auth: ApiAuthContext | None, tenant_id) -> None:
    # Direct function calls in tests bypass FastAPI dependency injection and
    # leave the Depends(...) sentinel (or None) in place of a real context;
    # those calls already bypass require_api_auth itself, so enforcement is
    # a no-op for anything that is not an ApiAuthContext.
    if not isinstance(auth, ApiAuthContext):
        return
    if not auth.tenant_allowed(tenant_id):
        _record_auth_rejection("tenant_forbidden")
        raise HTTPException(status_code=403, detail="tenant not authorized for this api key")


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
