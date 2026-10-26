from __future__ import annotations

"""Shared state for several API nodes (6-6).

Single node (default): everything stays where it was (files, SQLite, process
memory). Several nodes behind a load balancer: point each store at a shared
service, one switch per store so they can move one at a time:

  RATE_LIMIT_BACKEND=redis          rate-limit windows        REDIS_URL
  ANSWER_CACHE_BACKEND=redis        answer cache              REDIS_URL
  SESSION_REVOCATION_BACKEND=redis  revoked OIDC/admin sessions REDIS_URL
  CONVERSATION_STORE_BACKEND=postgres  conversation history   POSTGRES_URL
  INGESTION_REGISTRY_BACKEND=postgres  jobs + collection owners POSTGRES_URL

Dependencies: requirements-state.txt (redis, psycopg, psycopg-pool). The
audit log stays a local, hash-chained file per node; ship it to the central
log store.

Connections (stage 10):
- Redis: one client per URL with timeouts (REDIS_SOCKET_TIMEOUT_SECONDS,
  default 2), retry with backoff on connection errors, a periodic health
  check of idle connections, TLS with rediss:// (REDIS_CA_CERT for a private
  CA).
- Postgres: a connection pool (psycopg-pool; POSTGRES_POOL_MIN/MAX, default
  1/10) with a server-side statement timeout (POSTGRES_STATEMENT_TIMEOUT_MS,
  default 5000). Without psycopg-pool, one connection per use as before.
- Outages: errors surface as StateBackendUnavailable (HTTP 503 in the API).
  Callers that can degrade do so: the rate limiter falls back to the local
  window, the answer cache is skipped; session revocation fails closed
  (SESSION_REVOCATION_FAIL_CLOSED, default true: a session that cannot be
  checked is refused).
- health() is shown on /admin/health and decides /ready.
"""

import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Iterator, Optional

import config

logger = logging.getLogger(__name__)

_LOCK = Lock()
_REDIS: Dict[str, Any] = {}
_PG_POOLS: Dict[str, Any] = {}
_REDIS_USERS = ("RATE_LIMIT_BACKEND", "ANSWER_CACHE_BACKEND", "SESSION_REVOCATION_BACKEND")
_PG_USERS = ("CONVERSATION_STORE_BACKEND", "INGESTION_REGISTRY_BACKEND")
_LAST_WARNING: Dict[str, float] = {}


class StateBackendUnavailable(RuntimeError):
    """The shared Redis/Postgres service cannot be reached (maps to HTTP 503)."""


def backend(name: str, default: str = "local") -> str:
    return str(os.getenv(name, default)).strip().lower() or default


def _float_env(name: str, default: float) -> float:
    try:
        return float(str(os.getenv(name, "")).strip() or default)
    except ValueError:
        return default


def warn_degraded(component: str, exc: BaseException) -> None:
    """Log at most once a minute per component and count it as a metric."""
    from webapi import metrics_registry

    metrics_registry.increment("state_backend_errors_total", component)
    now = time.monotonic()
    if now - _LAST_WARNING.get(component, 0.0) >= 60.0:
        _LAST_WARNING[component] = now
        logger.warning("state backend degraded (%s): %s", component, type(exc).__name__)


# --- Redis -------------------------------------------------------------------------------


def _redis_kwargs(url: str) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {
        "socket_timeout": _float_env("REDIS_SOCKET_TIMEOUT_SECONDS", 2.0),
        "socket_connect_timeout": _float_env("REDIS_CONNECT_TIMEOUT_SECONDS", 2.0),
        "health_check_interval": int(_float_env("REDIS_HEALTH_CHECK_INTERVAL_SECONDS", 30)),
        "client_name": "kuraden",
    }
    try:
        from redis.backoff import ExponentialBackoff
        from redis.exceptions import ConnectionError as RedisConnectionError
        from redis.exceptions import TimeoutError as RedisTimeoutError
        from redis.retry import Retry

        kwargs["retry"] = Retry(ExponentialBackoff(cap=0.5, base=0.05), int(_float_env("REDIS_RETRIES", 2)))
        kwargs["retry_on_error"] = [RedisConnectionError, RedisTimeoutError]
    except ImportError:  # old redis-py: plain retry on timeout
        kwargs["retry_on_timeout"] = True
    ca = str(os.getenv("REDIS_CA_CERT", "")).strip()
    if url.startswith("rediss://") and ca:
        kwargs["ssl_ca_certs"] = ca
    return kwargs


def redis_client():
    url = str(os.getenv("REDIS_URL", "")).strip()
    if not url:
        raise RuntimeError("REDIS_URL is required for a redis backend")
    with _LOCK:
        client = _REDIS.get(url)
        if client is None:
            import redis

            try:
                client = redis.Redis.from_url(url, **_redis_kwargs(url))
            except TypeError:  # an option this redis-py does not know
                client = redis.Redis.from_url(url, socket_timeout=2.0, socket_connect_timeout=2.0)
            _REDIS[url] = client
        return client


def redis_errors() -> tuple:
    """Exception types that mean "Redis is unreachable" (for except clauses)."""
    try:
        from redis.exceptions import RedisError

        return (RedisError, OSError)
    except ImportError:
        return (OSError,)


# --- Postgres ----------------------------------------------------------------------------


def _pg_connect_kwargs() -> Dict[str, Any]:
    timeout_ms = int(_float_env("POSTGRES_STATEMENT_TIMEOUT_MS", 5000))
    return {
        "connect_timeout": int(_float_env("POSTGRES_CONNECT_TIMEOUT_SECONDS", 5)),
        "options": f"-c statement_timeout={timeout_ms}",
        "application_name": "kuraden",
    }


def _pg_pool(url: str):
    try:
        from psycopg_pool import ConnectionPool
    except ImportError:
        return None
    with _LOCK:
        pool = _PG_POOLS.get(url)
        if pool is None:
            kwargs = dict(
                conninfo=url,
                min_size=int(_float_env("POSTGRES_POOL_MIN", 1)),
                max_size=int(_float_env("POSTGRES_POOL_MAX", 10)),
                timeout=_float_env("POSTGRES_POOL_TIMEOUT_SECONDS", 10.0),
                kwargs=_pg_connect_kwargs(),
                open=True,
            )
            try:
                # Broken connections (server restart, failover) are replaced
                # before use instead of failing the request (psycopg-pool >= 3.2).
                pool = ConnectionPool(check=ConnectionPool.check_connection, **kwargs)
            except (TypeError, AttributeError):
                pool = ConnectionPool(**kwargs)
            _PG_POOLS[url] = pool
        return pool


def _pg_errors() -> tuple:
    try:
        import psycopg

        errors: tuple = (psycopg.OperationalError,)
    except ImportError:
        errors = ()
    try:
        from psycopg_pool import PoolTimeout

        errors = errors + (PoolTimeout,)
    except ImportError:
        pass
    return errors


@contextmanager
def postgres() -> Iterator[Any]:
    """Connection with commit on success, rollback on error (psycopg 3)."""
    url = str(os.getenv("POSTGRES_URL", "")).strip()
    if not url:
        raise RuntimeError("POSTGRES_URL is required for a postgres backend")
    unavailable = _pg_errors()
    pool = _pg_pool(url)
    try:
        if pool is not None:
            with pool.connection() as conn:
                yield conn
            return
        import psycopg

        conn = psycopg.connect(url, **_pg_connect_kwargs())
        try:
            with conn:
                yield conn
        finally:
            conn.close()
    except unavailable as exc:  # type: ignore[misc]
        warn_degraded("postgres", exc)
        raise StateBackendUnavailable("postgres is unavailable") from exc


def close_all() -> None:
    """Close pools and clients (application shutdown, tests)."""
    with _LOCK:
        for pool in _PG_POOLS.values():
            try:
                pool.close()
            except Exception:  # noqa: BLE001
                pass
        _PG_POOLS.clear()
        for client in _REDIS.values():
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
        _REDIS.clear()


def health() -> Dict[str, Any]:
    """Reachability of the shared services that are configured."""
    out: Dict[str, Any] = {}
    redis_users = [n for n in _REDIS_USERS if backend(n) == "redis"]
    if redis_users:
        started = time.monotonic()
        try:
            redis_client().ping()
            out["redis"] = {"ok": True, "ms": round((time.monotonic() - started) * 1000, 1), "used_by": redis_users}
        except Exception as exc:  # noqa: BLE001
            out["redis"] = {"ok": False, "error": type(exc).__name__, "used_by": redis_users}
    pg_users = [n for n in _PG_USERS if backend(n) == "postgres"]
    if pg_users:
        started = time.monotonic()
        try:
            with postgres() as conn:
                conn.execute("SELECT 1")
            out["postgres"] = {"ok": True, "ms": round((time.monotonic() - started) * 1000, 1), "used_by": pg_users}
        except Exception as exc:  # noqa: BLE001
            cause = exc.__cause__ or exc
            out["postgres"] = {"ok": False, "error": type(cause).__name__, "used_by": pg_users}
    return out


# --- session revocation (OIDC 6-3, admin browser sessions stage 10) ----------------------


def _sqlite_path() -> Path:
    return Path(config.RUNS_DIR) / "state" / "session_revocations.sqlite3"


@contextmanager
def _sqlite() -> Iterator[sqlite3.Connection]:
    path = _sqlite_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        conn = sqlite3.connect(str(path), timeout=30)
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS revoked_sessions (sid TEXT PRIMARY KEY, expires_at REAL NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS revoked_users (fingerprint TEXT PRIMARY KEY, revoked_before REAL NOT NULL)")
            with conn:
                yield conn
        finally:
            conn.close()


def _revocation_fail_closed() -> bool:
    return str(os.getenv("SESSION_REVOCATION_FAIL_CLOSED", "true")).strip().lower() not in {"0", "false", "no", "off"}


def revoke_session(sid: str, expires_at: float) -> None:
    """Reject this session id until it would have expired anyway."""
    if not sid:
        return
    ttl = max(1, int(expires_at - time.time()))
    if backend("SESSION_REVOCATION_BACKEND") == "redis":
        try:
            redis_client().set(f"revoked:sid:{sid}", "1", ex=ttl)
        except redis_errors() as exc:
            warn_degraded("redis_revocation", exc)
            raise StateBackendUnavailable("redis is unavailable") from exc
        return
    with _sqlite() as conn:
        conn.execute("DELETE FROM revoked_sessions WHERE expires_at < ?", (time.time(),))
        conn.execute("INSERT OR REPLACE INTO revoked_sessions (sid, expires_at) VALUES (?, ?)", (sid, float(expires_at)))


def revoke_user_sessions(fingerprint: str, *, before: Optional[float] = None, keep_seconds: int = 86400) -> None:
    """Reject every session of this identity issued before `before` (now)."""
    if not fingerprint:
        return
    stamp = float(before if before is not None else time.time())
    if backend("SESSION_REVOCATION_BACKEND") == "redis":
        try:
            redis_client().set(f"revoked:fp:{fingerprint}", str(stamp), ex=keep_seconds)
        except redis_errors() as exc:
            warn_degraded("redis_revocation", exc)
            raise StateBackendUnavailable("redis is unavailable") from exc
        return
    with _sqlite() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO revoked_users (fingerprint, revoked_before) VALUES (?, ?)", (fingerprint, stamp)
        )


def is_session_revoked(sid: Optional[str], fingerprint: Optional[str], issued_at: Optional[float]) -> bool:
    if not sid and not fingerprint:
        return False
    if backend("SESSION_REVOCATION_BACKEND") == "redis":
        try:
            client = redis_client()
            if sid and client.exists(f"revoked:sid:{sid}"):
                return True
            if fingerprint and issued_at is not None:
                raw = client.get(f"revoked:fp:{fingerprint}")
                return raw is not None and float(raw) >= float(issued_at)
            return False
        except redis_errors() as exc:
            warn_degraded("redis_revocation", exc)
            # Unknown state: refuse the session (default) or accept it.
            return _revocation_fail_closed()
    path = _sqlite_path()
    if not path.exists():
        return False
    with _sqlite() as conn:
        if sid and conn.execute("SELECT 1 FROM revoked_sessions WHERE sid = ? AND expires_at >= ?", (sid, time.time())).fetchone():
            return True
        if fingerprint and issued_at is not None:
            row = conn.execute("SELECT revoked_before FROM revoked_users WHERE fingerprint = ?", (fingerprint,)).fetchone()
            return row is not None and float(row[0]) >= float(issued_at)
    return False
