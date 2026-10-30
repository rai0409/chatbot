"""Daily limits on the use of the LLM, by person, tenant and for the whole installation (S6).

A limit says how much may be used in a day: calls or tokens by one caller, tokens or cost by one tenant, cost by everyone. When
a limit is reached the next LLM call is refused before it is made, and the answer stays extractive (the callers already handle
a refused call that way). The screens and the alerts warn from LLM_QUOTA_WARN_RATIO of a limit on.

  user_daily_calls     calls of one caller          user_daily_tokens     input and output tokens of one caller
  tenant_daily_tokens  tokens of one tenant         tenant_daily_cost     estimated cost of one tenant
  global_daily_cost    estimated cost of everyone

Where a limit comes from: the tenant's own value, else the global value (both set in the console, runtime_settings, with a reason),
else the environment default (LLM_QUOTA_*; 0 = no limit). A tenant may have its own user and tenant limits; the global cost is one value.

What was used is read from the usage files (rag_core/llm_usage.py), which every worker writes, and kept for a few seconds; each
call adds its own use at once. So several workers or nodes agree to within a few seconds, and one call may go a little over a limit
(its size is not known before it is made). Who is "a caller": the fingerprint of the API key, or of the person with single sign-on
(see audit_log.set_audit_actor). One key shared by many people is one caller; without an identified caller only the tenant and global
limits apply. The day is the local day of the business hours.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import config
from rag_core import llm_usage, runtime_settings

# name -> (who it limits, what it counts)
LIMITS = {
    "user_daily_calls": ("user", "calls"),
    "user_daily_tokens": ("user", "tokens"),
    "tenant_daily_tokens": ("tenant", "tokens"),
    "tenant_daily_cost": ("tenant", "cost"),
    "global_daily_cost": ("global", "cost"),
}
GLOBAL_ONLY = ("global_daily_cost",)
KEY_PREFIX = "llm.quota."

_LOCK = threading.Lock()
_CACHE: Dict[str, Any] = {"day": None, "at": 0.0, "users": {}, "tenants": {}, "global": {}}


class QuotaExceeded(RuntimeError):
    """A limit is reached: the LLM is not called. `.limit` names it, `.scope` says who it limits."""

    def __init__(self, limit: str, scope: str, allowed: float, used: float):
        super().__init__(f"the daily limit '{limit}' is reached")
        self.limit, self.scope, self.allowed, self.used = limit, scope, allowed, used


def _zero() -> Dict[str, float]:
    return {"calls": 0, "tokens": 0, "cost": 0.0}


def _local_day_start(now: datetime) -> datetime:
    offset = timedelta(hours=float(getattr(config, "BUSINESS_HOURS_UTC_OFFSET", 9.0)))
    local = now + offset
    return local.replace(hour=0, minute=0, second=0, microsecond=0) - offset


def reset() -> None:
    with _LOCK:
        _CACHE.update(day=None, at=0.0, users={}, tenants={}, **{"global": {}})


def _add(bucket: Dict[str, float], calls: int, tokens: int, cost: float) -> None:
    bucket["calls"] += calls
    bucket["tokens"] += tokens
    bucket["cost"] += cost


def _load(now: datetime) -> None:
    """Add up today's calls from the usage files and keep the result for LLM_QUOTA_CACHE_SECONDS."""
    start = _local_day_start(now)
    users: Dict[tuple, Dict[str, float]] = {}
    tenants: Dict[str, Dict[str, float]] = {}
    total = _zero()
    for row in llm_usage.read(2, today=now.date()):
        if row.get("event") != "call" or row.get("outcome") not in ("ok", "error"):
            continue
        try:
            if datetime.fromisoformat(row["t"]) < start:
                continue
        except (KeyError, ValueError):
            continue
        tokens = int(row.get("input_tokens") or 0) + int(row.get("output_tokens") or 0)
        cost = float(row.get("cost") or 0.0)
        tenant = str(row.get("tenant_id") or "")
        _add(tenants.setdefault(tenant, _zero()), 1, tokens, cost)
        _add(total, 1, tokens, cost)
        if row.get("key_fingerprint"):
            _add(users.setdefault((tenant, str(row["key_fingerprint"])), _zero()), 1, tokens, cost)
    _CACHE.update(day=start, at=time.monotonic(), users=users, tenants=tenants, **{"global": total})


def _fresh(now: datetime) -> None:
    ttl = float(getattr(config, "LLM_QUOTA_CACHE_SECONDS", 10.0))
    with _LOCK:
        if _CACHE["day"] != _local_day_start(now) or time.monotonic() - float(_CACHE["at"]) >= ttl:
            _load(now)


def note(tenant_id: str, user: Optional[str], *, tokens: int = 0, cost: float = 0.0) -> None:
    """Add one call to today's use at once (the next read of the files will include it as well)."""
    with _LOCK:
        if _CACHE["day"] is None:
            return  # nothing is loaded yet: the first check reads the files, which already hold this call
        _add(_CACHE["tenants"].setdefault(tenant_id, _zero()), 1, tokens, cost)
        _add(_CACHE["global"], 1, tokens, cost)
        if user:
            _add(_CACHE["users"].setdefault((tenant_id, user), _zero()), 1, tokens, cost)


def env_default(name: str) -> float:
    return float(getattr(config, "LLM_QUOTA_" + name.upper(), 0) or 0)


def limit_for(name: str, tenant_id: Optional[str] = None) -> Dict[str, Any]:
    """The limit in force: its value (0 = none) and where it comes from (tenant, global, environment)."""
    if name not in LIMITS:
        raise KeyError(name)
    key = KEY_PREFIX + name
    if tenant_id and name not in GLOBAL_ONLY:
        entry = runtime_settings.entry(key, tenant_id=tenant_id)
        if entry is not None and entry.get("value") is not None:
            return {"value": float(entry["value"]), "source": "tenant"}
    entry = runtime_settings.entry(key)
    if entry is not None and entry.get("value") is not None:
        return {"value": float(entry["value"]), "source": "global"}
    return {"value": env_default(name), "source": "environment"}


def set_limit(name: str, value: Optional[float], *, by: str, reason: str, tenant_id: Optional[str] = None) -> Dict[str, Any]:
    """Set a limit (None removes it, so the next level applies). 0 means no limit."""
    if name not in LIMITS:
        raise runtime_settings.SettingsError(f"the limit is one of: {', '.join(LIMITS)}")
    if tenant_id and name in GLOBAL_ONLY:
        raise runtime_settings.SettingsError(f"'{name}' is one value for the whole installation")
    if value is not None:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise runtime_settings.SettingsError("a limit is a number, 0 or more (0 = no limit)")
    return runtime_settings.set_value(KEY_PREFIX + name, value, by=by, reason=reason, tenant_id=tenant_id)


def _used(name: str, tenant_id: str, user: Optional[str]) -> Optional[float]:
    who, what = LIMITS[name]
    if who == "user":
        if not user:
            return None  # no identified caller: a limit on callers cannot be applied
        return float(_CACHE["users"].get((tenant_id, user), _zero())[what])
    if who == "tenant":
        return float(_CACHE["tenants"].get(tenant_id, _zero())[what])
    return float(_CACHE["global"].get(what, 0))


def check(feature: str, tenant_id: str, user: Optional[str], *, now: Optional[datetime] = None) -> None:
    """Raise QuotaExceeded when a limit that applies to this call is reached."""
    if feature in (getattr(config, "LLM_QUOTA_EXEMPT_FEATURES", None) or []):
        return
    clock = now or datetime.now(timezone.utc)
    _fresh(clock)
    for name, (who, _what) in LIMITS.items():
        allowed = limit_for(name, tenant_id)["value"]
        if allowed <= 0:
            continue
        used = _used(name, tenant_id, user)
        if used is not None and used >= allowed:
            raise QuotaExceeded(name, who, allowed, used)


def snapshot(tenant_id: Optional[str] = None, *, tenants: Optional[List[str]] = None, now: Optional[datetime] = None,
             top: int = 50) -> Dict[str, Any]:
    """Today's use against the limits, for the console. `tenants` limits it to an administrator's tenants (None: all)."""
    clock = now or datetime.now(timezone.utc)
    _fresh(clock)
    warn = float(getattr(config, "LLM_QUOTA_WARN_RATIO", 0.8))
    allowed_tenants = set(tenants) if tenants is not None else None
    with _LOCK:
        tenant_rows = {t: dict(v) for t, v in _CACHE["tenants"].items() if allowed_tenants is None or t in allowed_tenants}
        user_rows = [{"tenant_id": t, "user": u, **dict(v)} for (t, u), v in _CACHE["users"].items()
                     if allowed_tenants is None or t in allowed_tenants]
        total = dict(_CACHE["global"])
    user_rows.sort(key=lambda r: (-r["tokens"], -r["calls"]))
    limits = []
    for name, (who, what) in LIMITS.items():
        base = limit_for(name, tenant_id)
        limits.append({"name": name, "scope": who, "unit": what, "value": base["value"], "source": base["source"],
                       "environment_default": env_default(name)})
    return {"day_starts_at": _local_day_start(clock).isoformat(), "warn_ratio": warn, "limits": limits, "tenants": tenant_rows,
            "users": user_rows[:top], "global": total, "warnings": warnings(tenant_rows, user_rows, total, warn)}


def warnings(tenant_rows: Dict[str, Dict[str, float]], user_rows: List[Dict[str, Any]], total: Dict[str, float], warn: float) -> List[Dict[str, Any]]:
    """Every limit in use at `warn` or more of its value (ratio 1 or more: reached)."""
    out: List[Dict[str, Any]] = []
    for name, (who, what) in LIMITS.items():
        if who == "global":
            allowed = limit_for(name)["value"]
            if allowed > 0 and float(total.get(what, 0)) / allowed >= warn:
                out.append({"limit": name, "scope": "global", "id": None, "used": float(total.get(what, 0)), "allowed": allowed,
                            "ratio": round(float(total.get(what, 0)) / allowed, 3)})
        elif who == "tenant":
            for tenant, row in tenant_rows.items():
                allowed = limit_for(name, tenant)["value"]
                if allowed > 0 and float(row[what]) / allowed >= warn:
                    out.append({"limit": name, "scope": "tenant", "id": tenant, "used": float(row[what]), "allowed": allowed,
                                "ratio": round(float(row[what]) / allowed, 3)})
        else:
            for row in user_rows:
                allowed = limit_for(name, row["tenant_id"])["value"]
                if allowed > 0 and float(row[what]) / allowed >= warn:
                    out.append({"limit": name, "scope": "user", "id": f"{row['tenant_id']}/{row['user']}", "used": float(row[what]),
                                "allowed": allowed, "ratio": round(float(row[what]) / allowed, 3)})
    return sorted(out, key=lambda r: -r["ratio"])
