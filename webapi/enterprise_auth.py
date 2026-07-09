from __future__ import annotations

# Default-OFF "trusted reverse-proxy header" enterprise auth bridge.
#
# This is the safe integration BOUNDARY only — not an IdP. The actual
# enterprise identity provider (SAML/OIDC/LDAP/AD/Okta/Azure AD) lives OUTSIDE
# the app, in front of it, in a reverse proxy or gateway. When enabled, that
# proxy authenticates the user and forwards a small set of trusted identity
# headers plus a shared trust signal; this module verifies the trust signal and
# maps the enterprise identity onto the EXISTING tenant-authorization context
# (ApiAuthContext) without broadening tenant access.
#
# Security model:
# - disabled by default; when disabled this returns None and the API-key path
#   runs unchanged (spoofed enterprise headers are simply never read).
# - when enabled, enterprise headers are honored ONLY if the request also
#   carries X-Enterprise-Auth-Trust matching ENTERPRISE_AUTH_TRUST_TOKEN.
# - missing/invalid trust signal or unmapped identity fails closed.
# - allowed tenants come solely from the mapped enterprise identity; the
#   request tenant_id is still checked against them by enforce_tenant_authorization
#   (so cross-tenant access is rejected and never broadened).
# - never logs/returns raw secrets, trust tokens, API keys, prompts, or docs;
#   only a sha256-derived identity fingerprint and stable enum labels are used.
#
# Reads process environment via os.getenv (same pattern as webapi.api_auth);
# it never opens or parses the .env file.

import hmac
import os
from collections.abc import Mapping
from typing import Optional

from fastapi import HTTPException

from webapi import metrics_registry
from webapi.api_auth import (
    ApiAuthContext,
    _ENABLED_VALUES,
    _key_fingerprint,
    _normalize_tenant,
    _parse_tenant_map,
    _record_auth_rejection,
)


_TRUST_HEADER = "x-enterprise-auth-trust"
_TENANT_HEADER = "x-enterprise-tenant"
_USER_HEADER = "x-enterprise-user"
_EMAIL_HEADER = "x-enterprise-email"
_GROUPS_HEADER = "x-enterprise-groups"
_IDENTITY_HEADERS = (_TENANT_HEADER, _USER_HEADER, _EMAIL_HEADER, _GROUPS_HEADER)


def enterprise_auth_enabled() -> bool:
    return str(os.getenv("ENTERPRISE_AUTH_ENABLED", "")).strip().lower() in _ENABLED_VALUES


def _trust_token() -> str:
    return str(os.getenv("ENTERPRISE_AUTH_TRUST_TOKEN", "")).strip()


def _tenant_map_raw() -> str:
    return str(os.getenv("ENTERPRISE_AUTH_TENANT_MAP", "")).strip()


def _header_get(headers: Mapping[str, str] | None, name: str) -> Optional[str]:
    if not headers:
        return None
    candidates = (
        name,
        name.lower(),
        name.upper(),
        "-".join(part.capitalize() for part in name.split("-")),
    )
    for candidate in candidates:
        try:
            value = headers.get(candidate)
        except Exception:
            value = None
        if value:
            return str(value)
    return None


def resolve_enterprise_auth(headers: Mapping[str, str] | None) -> Optional[ApiAuthContext]:
    # Returns None when enterprise auth is disabled or the request is not an
    # enterprise-auth attempt (so the caller falls back to the unchanged API
    # key path). Raises HTTPException (fail closed) on a malformed/invalid
    # enterprise-auth attempt. Returns an ApiAuthContext on success.
    if not enterprise_auth_enabled():
        return None

    trust = _header_get(headers, _TRUST_HEADER)
    has_identity = any(_header_get(headers, h) for h in _IDENTITY_HEADERS)
    if not trust and not has_identity:
        # No enterprise signal at all -> not an enterprise attempt; let the API
        # key path handle it so existing key clients keep working.
        return None

    configured = _trust_token()
    if not configured:
        # Enterprise mode on but no trust token configured: refuse to trust any
        # forwarded header (fail closed, misconfiguration).
        raise HTTPException(status_code=503, detail="enterprise auth is not configured")

    if not trust:
        _record_auth_rejection("enterprise_trust_missing")
        raise HTTPException(status_code=401, detail="enterprise auth trust signal required")
    if not hmac.compare_digest(str(trust), configured):
        _record_auth_rejection("enterprise_trust_invalid")
        raise HTTPException(status_code=403, detail="invalid enterprise auth trust signal")

    raw_map = _tenant_map_raw()
    if not raw_map:
        _record_auth_rejection("enterprise_tenant_unmapped")
        raise HTTPException(status_code=403, detail="enterprise tenant mapping not configured")

    enterprise_tenant = _normalize_tenant(_header_get(headers, _TENANT_HEADER))
    tenant_map = _parse_tenant_map(raw_map)
    allowed: frozenset[str] = frozenset()
    for identity_key, tenants in tenant_map.items():
        if hmac.compare_digest(enterprise_tenant, _normalize_tenant(identity_key)):
            allowed = allowed | tenants
    if not allowed:
        _record_auth_rejection("enterprise_tenant_unmapped")
        raise HTTPException(status_code=403, detail="enterprise identity not authorized for any tenant")

    # Identity for rate-limit bucketing / audit: a sha256-derived fingerprint of
    # the enterprise user/email (never the raw value), falling back to the
    # enterprise tenant. No raw identity, trust token, or key is retained.
    identity = (
        _header_get(headers, _USER_HEADER)
        or _header_get(headers, _EMAIL_HEADER)
        or enterprise_tenant
    )
    metrics_registry.increment("api_enterprise_auth_total", "accepted")
    return ApiAuthContext(
        authenticated=True,
        tenant_authorization_enabled=True,
        allowed_tenants=allowed,
        key_fingerprint=_key_fingerprint(str(identity)),
    )
