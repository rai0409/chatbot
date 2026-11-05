from __future__ import annotations

"""OIDC hardening helpers (6-3), used by webapi.oidc_auth.

- discovery: endpoints from <issuer>/.well-known/openid-configuration instead
  of guessing "<issuer>/authorize|token|jwks" (OIDC_DISCOVERY=true)
- JWKS cache: keys are cached (OIDC_JWKS_TTL_SECONDS, default 3600) and
  refetched once when a token carries an unknown kid (key rotation), at most
  every OIDC_JWKS_MIN_REFRESH_SECONDS (default 60)
- azp: with several audiences, or when azp is present, azp must be our client
- session secret: at least 32 bytes

Network calls go through _http_get_json so tests can replace it.
"""

import json
import os
import time
from threading import Lock
from typing import Any, Callable, Dict, Optional

MIN_SESSION_SECRET_BYTES = 32
_DISCOVERY_TTL_SECONDS = 3600.0


def _float_env(name: str, default: float) -> float:
    try:
        return float(str(os.getenv(name, "")).strip() or default)
    except ValueError:
        return default


def _http_get_json(url: str) -> Dict[str, Any]:
    import requests

    # OIDC_CA_BUNDLE: CA file for an IdP behind a private CA (on-prem).
    verify = str(os.getenv("OIDC_CA_BUNDLE", "")).strip() or True
    try:
        resp = requests.get(url, timeout=15, verify=verify)
    except (requests.ConnectionError, requests.Timeout):
        time.sleep(0.5)
        resp = requests.get(url, timeout=15, verify=verify)
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, dict):
        raise ValueError("unexpected OIDC metadata response")
    return payload


def discovery_enabled() -> bool:
    return str(os.getenv("OIDC_DISCOVERY", "")).strip().lower() in {"1", "true", "yes", "on"}


_DISCOVERY_LOCK = Lock()
_DISCOVERY_CACHE: Dict[str, tuple[float, Dict[str, Any]]] = {}


def discover(issuer: str, *, fetch: Callable[[str], Dict[str, Any]] | None = None, now: Callable[[], float] = time.time) -> Dict[str, str]:
    """Endpoints from the provider metadata; the metadata issuer must match."""
    base = issuer.rstrip("/")
    with _DISCOVERY_LOCK:
        cached = _DISCOVERY_CACHE.get(base)
        if cached and now() - cached[0] < _DISCOVERY_TTL_SECONDS:
            return dict(cached[1])
    try:
        metadata = (fetch or _http_get_json)(base + "/.well-known/openid-configuration")
    except Exception:
        if cached:
            # IdP metadata temporarily unreachable: keep using the last good copy.
            return dict(cached[1])
        raise
    if str(metadata.get("issuer") or "").rstrip("/") != base:
        raise ValueError("OIDC discovery issuer mismatch")
    endpoints = {
        "auth_endpoint": str(metadata.get("authorization_endpoint") or ""),
        "token_endpoint": str(metadata.get("token_endpoint") or ""),
        "jwks_uri": str(metadata.get("jwks_uri") or ""),
        "end_session_endpoint": str(metadata.get("end_session_endpoint") or ""),
    }
    if not all(endpoints[k] for k in ("auth_endpoint", "token_endpoint", "jwks_uri")):
        raise ValueError("OIDC discovery document lacks required endpoints")
    with _DISCOVERY_LOCK:
        _DISCOVERY_CACHE[base] = (now(), endpoints)
    return dict(endpoints)


class JwksCache:
    def __init__(self, fetch: Callable[[str], Dict[str, Any]] | None = None, now: Callable[[], float] = time.time):
        self._fetch = fetch or _http_get_json
        self._now = now
        self._lock = Lock()
        self._entries: Dict[str, Dict[str, Any]] = {}

    def _load(self, uri: str) -> Dict[str, Any]:
        jwks = self._fetch(uri)
        if not isinstance(jwks.get("keys"), list):
            raise ValueError("JWKS document has no keys")
        entry = {"jwks": jwks, "fetched_at": self._now()}
        self._entries[uri] = entry
        return entry

    def get(self, uri: str, kid: Optional[str] = None, *, force: bool = False) -> Dict[str, Any]:
        # force=True refetches now (one retry after a failed signature check;
        # the caller bounds it to once per login callback).
        ttl = _float_env("OIDC_JWKS_TTL_SECONDS", 3600.0)
        min_refresh = _float_env("OIDC_JWKS_MIN_REFRESH_SECONDS", 60.0)
        with self._lock:
            entry = self._entries.get(uri)
            if force or entry is None or self._now() - entry["fetched_at"] >= ttl:
                entry = self._load(uri)
            if kid and not any(k.get("kid") == kid for k in entry["jwks"]["keys"]):
                # Unknown kid: the provider may have rotated keys. Refetch,
                # but not more often than min_refresh (no fetch amplification
                # from forged tokens).
                if self._now() - entry["fetched_at"] >= min_refresh:
                    entry = self._load(uri)
            return entry["jwks"]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


jwks_cache = JwksCache()


def token_kid(id_token: str) -> Optional[str]:
    import base64

    try:
        header_b64 = id_token.split(".", 1)[0]
        header = json.loads(base64.urlsafe_b64decode(header_b64 + "=" * (-len(header_b64) % 4)))
    except Exception:  # noqa: BLE001
        return None
    kid = header.get("kid") if isinstance(header, dict) else None
    return str(kid) if kid else None


def validate_azp(claims: Dict[str, Any], client_id: str) -> None:
    aud = claims.get("aud")
    audiences = aud if isinstance(aud, list) else [aud]
    azp = claims.get("azp")
    if len([a for a in audiences if a]) > 1 and not azp:
        raise ValueError("oidc token with several audiences lacks azp")
    if azp is not None and str(azp) != str(client_id):
        raise ValueError("oidc azp does not match the client")


def require_session_secret(secret: str) -> str:
    if len(str(secret or "").encode("utf-8")) < MIN_SESSION_SECRET_BYTES:
        raise ValueError(f"OIDC_SESSION_SECRET must be at least {MIN_SESSION_SECRET_BYTES} bytes")
    return secret
