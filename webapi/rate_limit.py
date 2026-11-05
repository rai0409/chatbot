from __future__ import annotations

import math
import os
import time
from threading import Lock
from typing import Callable, Dict, Tuple

import hashlib

from fastapi import Depends, HTTPException, Request

from webapi import metrics_registry
from webapi.api_auth import ApiAuthContext, require_api_auth


_ENABLED_VALUES = {"1", "true", "yes", "on"}

_DEFAULT_REQUESTS_PER_MINUTE = 60
_WINDOW_SECONDS = 60.0

# Bucket for requests that carry no key fingerprint (API auth disabled).
# With auth enabled, unauthenticated requests are rejected before the
# limiter runs and never consume budget.
_ANONYMOUS_BUCKET = "anonymous"


def rate_limit_enabled() -> bool:
    return str(os.getenv("RATE_LIMIT_ENABLED", "")).strip().lower() in _ENABLED_VALUES


def rate_limit_requests_per_minute() -> int:
    raw = str(os.getenv("RATE_LIMIT_REQUESTS_PER_MINUTE", "")).strip()
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_REQUESTS_PER_MINUTE
    if value < 1:
        return _DEFAULT_REQUESTS_PER_MINUTE
    return value


class FixedWindowRateLimiter:
    # In-process, thread-safe fixed-window counter. With multiple uvicorn
    # workers each process enforces its own budget (same caveat as the
    # metrics counters); the effective limit is per worker, not global.
    # Buckets are key fingerprints (bounded by the configured key set plus
    # the anonymous bucket) — never raw API keys.

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = Lock()
        # bucket -> (window_index, request_count)
        self._windows: Dict[str, Tuple[int, int]] = {}

    def check(
        self, bucket: str, limit: int, window_seconds: float = _WINDOW_SECONDS
    ) -> Tuple[bool, float]:
        # Returns (allowed, retry_after_seconds). retry_after_seconds is the
        # time remaining until the current window rolls over (0.0 when allowed).
        now = self._clock()
        window_index = int(now // window_seconds)
        with self._lock:
            # Drop finished windows so the table does not grow with every
            # client ever seen (per-IP buckets).
            if len(self._windows) > 1024:
                self._windows = {k: v for k, v in self._windows.items() if v[0] >= window_index}
            current_index, count = self._windows.get(bucket, (window_index, 0))
            if current_index != window_index:
                count = 0
            count += 1
            self._windows[bucket] = (window_index, count)
        if count > int(limit):
            retry_after = (window_index + 1) * window_seconds - now
            return False, max(retry_after, 0.0)
        return True, 0.0

    def reset(self) -> None:
        with self._lock:
            self._windows.clear()

    def snapshot(self) -> Dict[str, Tuple[int, int]]:
        with self._lock:
            return dict(self._windows)


_limiter = FixedWindowRateLimiter()


class RedisRateLimiter:
    """Fixed window shared by every API node (RATE_LIMIT_BACKEND=redis)."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock

    def check(self, bucket: str, limit: int, window_seconds: float = _WINDOW_SECONDS) -> Tuple[bool, float]:
        from webapi import state_backend

        try:
            return self._check(bucket, limit, window_seconds)
        except state_backend.redis_errors() as exc:
            # Redis down: keep limiting with this node's own window instead
            # of failing every request (stage 10).
            state_backend.warn_degraded("redis_rate_limit", exc)
            return _limiter.check(bucket, limit, window_seconds)

    def _check(self, bucket: str, limit: int, window_seconds: float) -> Tuple[bool, float]:
        from webapi import state_backend

        now = self._clock()
        window_index = int(now // window_seconds)
        key = f"rl:{bucket}:{window_index}"
        client = state_backend.redis_client()
        pipe = client.pipeline()
        pipe.incr(key)
        pipe.expire(key, int(window_seconds * 2))
        count = int(pipe.execute()[0])
        if count > int(limit):
            return False, max((window_index + 1) * window_seconds - now, 0.0)
        return True, 0.0


def _active_limiter():
    from webapi import state_backend

    if state_backend.backend("RATE_LIMIT_BACKEND") == "redis":
        return RedisRateLimiter()
    return _limiter


def reset_limiter() -> None:
    _limiter.reset()


def anonymous_by_ip() -> bool:
    # RATE_LIMIT_ANONYMOUS_BY_IP=true: unauthenticated callers get one bucket
    # per client address (behind a proxy, run uvicorn with --proxy-headers
    # and --forwarded-allow-ips so the address is the real client).
    return str(os.getenv("RATE_LIMIT_ANONYMOUS_BY_IP", "")).strip().lower() in _ENABLED_VALUES


def _bucket_for(auth, client_host: str | None = None) -> str:
    if isinstance(auth, ApiAuthContext) and auth.key_fingerprint:
        return auth.key_fingerprint
    if client_host and anonymous_by_ip():
        return "ip:" + hashlib.sha256(client_host.encode("utf-8")).hexdigest()[:12]
    return _ANONYMOUS_BUCKET


def enforce_rate_limit(
    auth,
    limiter: FixedWindowRateLimiter | None = None,
    *,
    client_host: str | None = None,
) -> None:
    if not rate_limit_enabled():
        return
    active = limiter if limiter is not None else _active_limiter()
    allowed, retry_after = active.check(_bucket_for(auth, client_host), rate_limit_requests_per_minute())
    if not allowed:
        # Observability only: label by auth-state, never the fingerprint or key.
        bucketed = "authenticated" if (
            isinstance(auth, ApiAuthContext) and auth.key_fingerprint
        ) else "anonymous"
        metrics_registry.increment("api_rate_limited_total", bucketed)
        raise HTTPException(
            status_code=429,
            detail="rate limit exceeded",
            headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
        )


def require_api_auth_rate_limited(
    request: Request,
    api_auth: ApiAuthContext = Depends(require_api_auth),
) -> ApiAuthContext:
    # Sub-dependency ordering guarantees enforcement AFTER authentication:
    # require_api_auth raises 401/403 before this body runs, so rejected
    # requests never consume rate-limit budget.
    client_host = request.client.host if request.client else None
    enforce_rate_limit(api_auth, client_host=client_host)
    return api_auth
