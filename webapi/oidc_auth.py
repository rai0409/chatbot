from __future__ import annotations

# Default-OFF in-app OIDC (OAuth2 Authorization Code + PKCE) login/session
# integration (Prompt046), per the Prompt045 decision. ID-token signatures are
# verified via JWKS using authlib/cryptography (no homegrown crypto). The
# session is a short HS256-signed cookie. When ENTERPRISE_OIDC_ENABLED is off
# this module is inert: resolve_oidc_session() returns None and the endpoints
# behave as absent, so the API-key path and the Prompt037 reverse-proxy bridge
# are unchanged.
#
# Reads process environment via os.getenv (same pattern as webapi.api_auth);
# never opens .env. Never logs/returns the client secret, session secret,
# tokens, or raw identity — only sha256-derived fingerprints and stable enums.

import base64
import hashlib
import os
import secrets
import time
from collections.abc import Mapping
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

import warnings as _warnings

with _warnings.catch_warnings():
    # authlib.jose is deprecated in favor of joserfc but remains supported and
    # is the pinned dependency; silence the import-time deprecation notice only.
    _warnings.simplefilter("ignore")
    from authlib.jose import JsonWebKey, JsonWebToken
    from authlib.jose.errors import JoseError
from fastapi import HTTPException

import config as app_config  # this module has its own config() function
from webapi import metrics_registry, oidc_hardening, state_backend
from webapi.api_auth import (
    ApiAuthContext,
    _ENABLED_VALUES,
    _key_fingerprint,
    _normalize_tenant,
    _parse_tenant_map,
    _record_auth_rejection,
)

_RS = JsonWebToken(["RS256"])
_HS = JsonWebToken(["HS256"])
# ID-token algorithms that may be enabled (OIDC_ID_TOKEN_ALGS, default
# "RS256,PS256,ES256"); "none" and HMAC algorithms are never accepted.
_ASYMMETRIC_ALGS = {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"}

SESSION_COOKIE = "kuraden_session"
TXN_COOKIE = "kuraden_oidc_txn"
_SESSION_TTL = 3600
_TXN_TTL = 600


def oidc_enabled() -> bool:
    return str(os.getenv("ENTERPRISE_OIDC_ENABLED", "")).strip().lower() in _ENABLED_VALUES


def cookie_secure() -> bool:
    # Default true (production behind TLS); set OIDC_COOKIE_SECURE=false only for
    # local/plain-HTTP testing.
    raw = str(os.getenv("OIDC_COOKIE_SECURE", "")).strip().lower()
    if raw == "":
        return True
    return raw in _ENABLED_VALUES


def _env(name: str) -> str:
    return str(os.getenv(name, "") or "").strip()


def _session_secret() -> str:
    return _env("OIDC_SESSION_SECRET")


def _session_ttl() -> int:
    try:
        return max(300, int(_env("OIDC_SESSION_TTL_SECONDS") or _SESSION_TTL))
    except ValueError:
        return _SESSION_TTL


def _clock_skew() -> int:
    # Allowed difference between this server's clock and the IdP's (exp/iat/nbf).
    try:
        return max(0, int(_env("OIDC_CLOCK_SKEW_SECONDS") or 60))
    except ValueError:
        return 60


def _id_token_jwt() -> JsonWebToken:
    algs = [a.strip().upper() for a in (_env("OIDC_ID_TOKEN_ALGS") or "RS256,PS256,ES256").split(",") if a.strip()]
    allowed = [a for a in algs if a in _ASYMMETRIC_ALGS] or ["RS256"]
    return JsonWebToken(allowed)


def http_verify():
    """requests "verify": OIDC_CA_BUNDLE for an IdP behind a private CA."""
    return _env("OIDC_CA_BUNDLE") or True


def config() -> Dict[str, str]:
    # Enabled but unconfigured -> fail closed (503). Returns non-secret + secret
    # config; callers must never log the secrets.
    issuer = _env("OIDC_ISSUER")
    client_id = _env("OIDC_CLIENT_ID")
    client_secret = _env("OIDC_CLIENT_SECRET")
    redirect_uri = _env("OIDC_REDIRECT_URI")
    auth_endpoint = _env("OIDC_AUTH_ENDPOINT") or (issuer.rstrip("/") + "/authorize" if issuer else "")
    token_endpoint = _env("OIDC_TOKEN_ENDPOINT") or (issuer.rstrip("/") + "/token" if issuer else "")
    jwks_uri = _env("OIDC_JWKS_URI") or (issuer.rstrip("/") + "/jwks" if issuer else "")
    if oidc_hardening.discovery_enabled() and issuer:
        # Endpoints from the provider metadata instead of guessed paths;
        # explicitly configured endpoints still win.
        found = oidc_hardening.discover(issuer)
        auth_endpoint = _env("OIDC_AUTH_ENDPOINT") or found["auth_endpoint"]
        token_endpoint = _env("OIDC_TOKEN_ENDPOINT") or found["token_endpoint"]
        jwks_uri = _env("OIDC_JWKS_URI") or found["jwks_uri"]
    required = {
        "issuer": issuer, "client_id": client_id, "client_secret": client_secret,
        "redirect_uri": redirect_uri, "auth_endpoint": auth_endpoint,
        "token_endpoint": token_endpoint, "jwks_uri": jwks_uri,
        "session_secret": _session_secret(),
    }
    if not all(required.values()):
        raise HTTPException(status_code=503, detail="oidc auth is not configured")
    return required


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _pkce() -> Tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


# --- signed transient/session tokens (HS256 via the session secret) ---------


def _sign(payload: Dict[str, Any], secret: str, ttl: int) -> str:
    claims = dict(payload)
    claims["exp"] = int(time.time()) + ttl
    return _HS.encode({"alg": "HS256"}, claims, secret).decode("ascii")


def _read_signed(token: str, secret: str) -> Optional[Dict[str, Any]]:
    if not token or not secret:
        return None
    try:
        claims = _HS.decode(token, secret)
        claims.validate()  # exp/iat/nbf
        return dict(claims)
    except JoseError:
        return None
    except Exception:
        return None


# --- login / callback helpers ----------------------------------------------


def build_login() -> Tuple[str, str]:
    # Returns (authorization_url, txn_cookie_value). state+nonce+PKCE are bound
    # in the signed txn cookie (CSRF + replay protection).
    cfg = config()
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier, challenge = _pkce()
    params = {
        "response_type": "code",
        "client_id": cfg["client_id"],
        "redirect_uri": cfg["redirect_uri"],
        "scope": _env("OIDC_SCOPE") or "openid profile email",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    auth_url = cfg["auth_endpoint"] + ("&" if "?" in cfg["auth_endpoint"] else "?") + urlencode(params)
    txn = _sign({"state": state, "nonce": nonce, "code_verifier": verifier}, cfg["session_secret"], _TXN_TTL)
    return auth_url, txn


def verify_id_token(id_token: str, *, jwks: Dict[str, Any], issuer: str, audience: str, nonce: str) -> Dict[str, Any]:
    # Verify signature against JWKS and validate iss/aud/exp + nonce + sub.
    claims = _id_token_jwt().decode(
        id_token,
        JsonWebKey.import_key_set(jwks),
        claims_options={
            "iss": {"essential": True, "value": issuer},
            "aud": {"essential": True, "value": audience},
        },
    )
    skew = _clock_skew()
    # Clock skew tolerance applies to "not yet valid" (nbf / iat in the
    # future); an expired token is refused without tolerance.
    claims.validate(leeway=skew)
    now = time.time()
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or exp <= now:
        raise ValueError("oidc token expired")
    iat = claims.get("iat")
    if isinstance(iat, (int, float)) and iat > now + skew:
        raise ValueError("oidc token issued in the future")
    oidc_hardening.validate_azp(dict(claims), audience)
    if str(claims.get("nonce") or "") != str(nonce or ""):
        raise ValueError("oidc nonce mismatch")
    if not str(claims.get("sub") or "").strip():
        raise ValueError("oidc token missing sub")
    return dict(claims)


def _exchange_code(token_endpoint: str, *, code: str, redirect_uri: str, client_id: str,
                   client_secret: str, code_verifier: str) -> Dict[str, Any]:
    # Isolated network call (monkeypatched in tests). Uses requests (already a
    # transitive dependency); never logs the client secret or tokens.
    import requests

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
    }
    auth = None
    # OIDC_TOKEN_AUTH_METHOD: client_secret_post (default) or client_secret_basic.
    if (_env("OIDC_TOKEN_AUTH_METHOD") or "client_secret_post") == "client_secret_basic":
        auth = (client_id, client_secret)
    else:
        data["client_secret"] = client_secret
    # The code is single-use: no retry here.
    resp = requests.post(token_endpoint, data=data, auth=auth, timeout=_http_timeout(), verify=http_verify())  # nosec B113 - the timeout is the OIDC_HTTP_TIMEOUT_SECONDS setting (at least 1 second)
    resp.raise_for_status()
    return resp.json()


def _http_timeout() -> float:
    # OIDC_HTTP_TIMEOUT_SECONDS (config.py); the former fixed value was 15 seconds.
    return max(1.0, float(getattr(app_config, "OIDC_HTTP_TIMEOUT_SECONDS", 15.0)))


# JWKS cache (TTL + refetch on unknown kid). The fetch goes through
# _fetch_jwks at call time, so tests that replace _fetch_jwks keep working.
_JWKS_CACHE = oidc_hardening.JwksCache(fetch=lambda uri: _fetch_jwks(uri))


def _fetch_jwks(jwks_uri: str) -> Dict[str, Any]:
    import requests

    for attempt in range(2):
        try:
            resp = requests.get(jwks_uri, timeout=_http_timeout(), verify=http_verify())  # nosec B113 - the timeout is the OIDC_HTTP_TIMEOUT_SECONDS setting (at least 1 second)
            resp.raise_for_status()
            return resp.json()
        except (requests.ConnectionError, requests.Timeout):
            if attempt:
                raise
            time.sleep(max(0.0, float(getattr(app_config, "OIDC_RETRY_WAIT_SECONDS", 0.5))))
    raise RuntimeError("unreachable")


def _allowed_tenants_for_claim(claims: Dict[str, Any]) -> Tuple[bool, frozenset]:
    # Map a tenant/group claim to allowed tenants via OIDC_TENANT_MAP. With no
    # map configured, authentication is allowed with tenant authorization
    # disabled (parity with the API-key no-map case); a configured map enforces
    # and fails closed on an unmapped identity (raised by the caller).
    raw_map = _env("OIDC_TENANT_MAP")
    if not raw_map:
        return False, frozenset()
    claim_name = _env("OIDC_TENANT_CLAIM") or "tenant"
    value = _normalize_tenant(claims.get(claim_name))
    tenant_map = _parse_tenant_map(raw_map)
    allowed: frozenset = frozenset()
    for key, tenants in tenant_map.items():
        if value == _normalize_tenant(key):
            allowed = allowed | tenants
    return True, allowed


def complete_login(*, code: str, state: str, txn_cookie: str) -> str:
    # Verifies state, exchanges the code, verifies the ID token, maps the tenant,
    # and returns a signed session cookie value. Fails closed (HTTPException).
    cfg = config()
    txn = _read_signed(txn_cookie, cfg["session_secret"])
    if not txn:
        _record_auth_rejection("oidc_state_invalid")
        raise HTTPException(status_code=400, detail="invalid or expired oidc transaction")
    if not state or not secrets.compare_digest(str(state), str(txn.get("state") or "")):
        _record_auth_rejection("oidc_state_invalid")
        raise HTTPException(status_code=400, detail="oidc state mismatch")

    try:
        token_response = _exchange_code(
            cfg["token_endpoint"], code=code, redirect_uri=cfg["redirect_uri"],
            client_id=cfg["client_id"], client_secret=cfg["client_secret"],
            code_verifier=str(txn.get("code_verifier") or ""),
        )
        id_token = str(token_response.get("id_token") or "")
        if not id_token:
            raise ValueError("token response missing id_token")
        kid = oidc_hardening.token_kid(id_token)
        verify_kwargs = {
            "issuer": cfg["issuer"], "audience": cfg["client_id"], "nonce": str(txn.get("nonce") or ""),
        }
        try:
            claims = verify_id_token(id_token, jwks=_JWKS_CACHE.get(cfg["jwks_uri"], kid), **verify_kwargs)
        except (JoseError, ValueError):
            # Keys may have rotated since they were cached: refetch once.
            claims = verify_id_token(id_token, jwks=_JWKS_CACHE.get(cfg["jwks_uri"], kid, force=True), **verify_kwargs)
    except HTTPException:
        raise
    except Exception:
        _record_auth_rejection("oidc_token_invalid")
        raise HTTPException(status_code=401, detail="oidc authentication failed")

    # RBAC (Prompt047): when a group->tenant / group->role map is configured,
    # derive allowed tenants + role from the IdP groups claim; otherwise fall
    # back to the single tenant claim with the default role.
    if _env("OIDC_GROUP_TENANT_MAP") or _env("OIDC_GROUP_ROLE_MAP"):
        from webapi import rbac

        groups_claim = _env("OIDC_GROUPS_CLAIM") or "groups"
        groups = claims.get(groups_claim)
        overage = claims.get("_claim_names")
        if groups is None and isinstance(overage, dict) and groups_claim in overage:
            # Microsoft Entra ID sends no groups when a user is in too many
            # (group overage). Use app roles (OIDC_GROUPS_CLAIM=roles) or
            # "groups assigned to the application" instead.
            _record_auth_rejection("oidc_groups_overage")
            raise HTTPException(status_code=403, detail="identity provider omitted the groups claim (group overage)")
        authz, allowed, role = rbac.resolve_role_and_tenants(groups)
    else:
        authz, allowed = _allowed_tenants_for_claim(claims)
        role = "user"

    if authz and not allowed:
        _record_auth_rejection("oidc_tenant_unmapped")
        raise HTTPException(status_code=403, detail="oidc identity not authorized for any tenant")

    fingerprint = _key_fingerprint(str(claims.get("sub")))
    metrics_registry.increment("api_oidc_auth_total", "accepted")
    metrics_registry.increment("api_role_total", str(role))
    session = _sign(
        {
            "fp": fingerprint, "authz": bool(authz), "tenants": "|".join(sorted(allowed)), "role": str(role),
            # sid / iat make a session revocable (logout, admin revoke).
            "sid": secrets.token_urlsafe(16), "iat": int(time.time()),
        },
        cfg["session_secret"], _session_ttl(),
    )
    return session


# --- session resolution into the existing auth context ----------------------


def _cookie(headers: Mapping[str, str] | None, name: str) -> Optional[str]:
    if not headers:
        return None
    raw = None
    for cand in ("cookie", "Cookie"):
        try:
            raw = headers.get(cand)
        except Exception:
            raw = None
        if raw:
            break
    if not raw:
        return None
    for part in str(raw).split(";"):
        if "=" in part:
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
    return None


def resolve_oidc_session(headers: Mapping[str, str] | None) -> Optional[ApiAuthContext]:
    # None when disabled or no valid session -> caller falls back to the API key
    # path (unchanged). An invalid/expired session is ignored (fail closed: it
    # grants nothing), never trusted.
    if not oidc_enabled():
        return None
    cookie = _cookie(headers, SESSION_COOKIE)
    if not cookie:
        return None
    data = _read_signed(cookie, _session_secret())
    if not data or not data.get("fp"):
        return None
    issued_at = data.get("iat")
    if issued_at is None and data.get("exp"):
        # Sessions from before sid/iat existed: issue time = expiry - lifetime,
        # so revoking the identity (logout, admin revoke) covers them too.
        issued_at = float(data["exp"]) - _session_ttl()
    if state_backend.is_session_revoked(data.get("sid"), str(data["fp"]), issued_at):
        _record_auth_rejection("oidc_session_revoked")
        return None
    authz = bool(data.get("authz"))
    tenants = str(data.get("tenants") or "")
    allowed = frozenset(t for t in tenants.split("|") if t) if authz else frozenset()
    return ApiAuthContext(
        authenticated=True,
        tenant_authorization_enabled=authz,
        allowed_tenants=allowed,
        key_fingerprint=str(data["fp"]),
        role=str(data.get("role") or "") or None,
    )


# --- session revocation (6-3) -------------------------------------------------


def revoke_session_from_headers(headers: Mapping[str, str] | None) -> bool:
    """Logout: the presented session can no longer be used, even if copied."""
    cookie = _cookie(headers, SESSION_COOKIE)
    data = _read_signed(cookie, _session_secret()) if cookie else None
    if not data:
        return False
    if data.get("sid"):
        state_backend.revoke_session(str(data["sid"]), float(data.get("exp") or time.time() + _session_ttl()))
        return True
    if data.get("fp"):
        # A session without sid cannot be revoked alone: end every session
        # of this identity issued until now (the user signs in again).
        revoke_identity(str(data["fp"]))
        return True
    return False


def revoke_identity(fingerprint: str) -> None:
    """Admin action: every session of this identity issued until now is rejected."""
    state_backend.revoke_user_sessions(fingerprint, keep_seconds=_session_ttl() + 60)


def end_session_info() -> Dict[str, str]:
    """RP-initiated logout at the IdP: {"end_session_url": ...} when known."""
    endpoint = _env("OIDC_END_SESSION_ENDPOINT")
    if not endpoint and oidc_hardening.discovery_enabled() and _env("OIDC_ISSUER"):
        try:
            endpoint = oidc_hardening.discover(_env("OIDC_ISSUER")).get("end_session_endpoint", "")
        except Exception:  # noqa: BLE001 - logout still ends the local session
            endpoint = ""
    if not endpoint:
        return {}
    params = {"client_id": _env("OIDC_CLIENT_ID")}
    if _env("OIDC_POST_LOGOUT_REDIRECT_URI"):
        params["post_logout_redirect_uri"] = _env("OIDC_POST_LOGOUT_REDIRECT_URI")
    return {"end_session_url": endpoint + ("&" if "?" in endpoint else "?") + urlencode(params)}
