"""The circuit breakers of the LLM endpoints, shared between workers and nodes through Redis (S7).

Without this each worker learns that an endpoint is down on its own: worker A opens its breaker, worker B keeps calling and
waiting for the timeout, and the banner on the screen comes and goes with the request. With BREAKER_BACKEND=redis (and
REDIS_URL, like the rate limiter and the answer cache) all workers read and change one state per breaker.

A change is one transaction (WATCH / MULTI / EXEC on the key), so two workers cannot both take the single trial call of a half-open
breaker. A state expires after a day without a change. If Redis cannot be reached, the breaker of that worker uses its own memory for
that call (circuit_breaker.FallbackStore) and the failure is logged once a minute: the LLM stays protected, only not shared.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, Optional, Tuple

from rag_core import circuit_breaker
from webapi import state_backend

PREFIX = "kuraden:breaker:"
TTL_SECONDS = 86400
_ATTEMPTS = 5


class RedisStore:
    """A circuit-breaker store on Redis. `client_factory` returns a redis client (a test passes a fake)."""

    def __init__(self, client_factory: Optional[Callable[[], Any]] = None, ttl_seconds: int = TTL_SECONDS):
        self._factory = client_factory or state_backend.redis_client
        self.ttl = int(ttl_seconds)

    def load(self, key: str) -> Optional[Dict[str, Any]]:
        raw = self._factory().get(PREFIX + key)
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def update(self, key: str, default: Dict[str, Any], fn: Callable[[Dict[str, Any]], Tuple[Dict[str, Any], Any]]) -> Any:
        client = self._factory()
        redis_key = PREFIX + key
        from redis.exceptions import WatchError

        for _attempt in range(_ATTEMPTS):
            with client.pipeline() as pipe:
                try:
                    pipe.watch(redis_key)
                    raw = pipe.get(redis_key)
                    try:
                        current = json.loads(raw) if raw else dict(default)
                    except ValueError:
                        current = dict(default)
                    if not isinstance(current, dict):
                        current = dict(default)
                    new_state, result = fn({**default, **current})
                    pipe.multi()
                    pipe.set(redis_key, json.dumps(new_state), ex=self.ttl)
                    pipe.execute()
                    return result
                except WatchError:
                    continue  # another worker changed the state meanwhile: read it again
        raise RuntimeError("the breaker state could not be changed (too many concurrent changes)")


_LAST_WARNING = {"at": 0.0}


def _warn(exc: BaseException) -> None:
    import time

    now = time.monotonic()
    if now - _LAST_WARNING["at"] >= 60.0:
        _LAST_WARNING["at"] = now
        logging.warning("shared circuit-breaker store unavailable (%s): this worker uses its own state", type(exc).__name__)


def install() -> bool:
    """Use Redis for the breakers when BREAKER_BACKEND=redis. Returns True when the shared store was set."""
    if state_backend.backend("BREAKER_BACKEND") != "redis":
        return False
    circuit_breaker.set_store(circuit_breaker.FallbackStore(RedisStore(), circuit_breaker.MemoryStore(), on_error=_warn))
    return True
