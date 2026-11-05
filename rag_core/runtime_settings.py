"""Runtime settings: the switches an administrator changes from the console without a restart (S1).

Where the settings of one deployment live:

  secrets (keys, passwords)            environment only, never in a file of the repository
  deployment-specific values           .env / config.py (endpoints, timeouts, thresholds, retention)
  runtime switches (this module)       a small store that the console changes; every change has a reason and an author
  code                                 definitions and logic only

The store is one JSON file (RUNTIME_SETTINGS_DIR, default <RUNS_DIR>/settings), replaced atomically under a lock that
every worker process shares. A change needs a reason, keeps its author and time, is kept in a short log inside the file,
and is written to the audit log (hash chain). A tenant value overrides the global one for that tenant.
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import config
from rag_core import audit_log

KEY_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
TENANT_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
MAX_LOG = 200
MAX_VALUE_CHARS = 2000
FILE_NAME = "runtime_settings.json"

_LOCK = threading.Lock()
_CACHE: Dict[str, Any] = {"key": None, "data": None}


class SettingsError(ValueError):
    """The change is not valid (a key, a tenant, a value or a missing reason)."""


class Conflict(SettingsError):
    """The settings changed since the caller read them (the revision is not the expected one)."""


def _dir() -> Path:
    configured = str(getattr(config, "RUNTIME_SETTINGS_DIR", "") or "").strip()
    return Path(configured) if configured else Path(config.RUNS_DIR) / "settings"


def _path() -> Path:
    return _dir() / FILE_NAME


def _empty() -> Dict[str, Any]:
    return {"rev": 0, "global": {}, "tenants": {}, "log": []}


def load() -> Dict[str, Any]:
    """The whole store (a copy). A missing or unreadable file is an empty store: every switch is at its default."""
    path = _path()
    try:
        stat = path.stat()
    except OSError:
        return _empty()
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    with _LOCK:
        if _CACHE["key"] == key and _CACHE["data"] is not None:
            return json.loads(json.dumps(_CACHE["data"]))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    merged = {**_empty(), **{k: data[k] for k in ("rev", "global", "tenants", "log") if k in data}}
    with _LOCK:
        _CACHE["key"], _CACHE["data"] = key, merged
    return json.loads(json.dumps(merged))


def revision() -> int:
    return int(load().get("rev") or 0)


def get(key: str, default: Any = None, *, tenant_id: Optional[str] = None) -> Any:
    """The value of `key`: the tenant's own value if there is one, else the global value, else `default`."""
    data = load()
    if tenant_id:
        entry = (data["tenants"].get(str(tenant_id)) or {}).get(key)
        if isinstance(entry, dict) and "value" in entry:
            return entry["value"]
    entry = data["global"].get(key)
    if isinstance(entry, dict) and "value" in entry:
        return entry["value"]
    return default


def entry(key: str, *, tenant_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The stored record (value, by, reason, at) of one key in one scope, or None."""
    data = load()
    bucket = (data["tenants"].get(str(tenant_id)) or {}) if tenant_id else data["global"]
    found = bucket.get(key)
    return dict(found) if isinstance(found, dict) else None


def _check_value(value: Any) -> None:
    scalar = (str, int, float, bool)
    ok = value is None or isinstance(value, scalar) or (isinstance(value, list) and all(isinstance(v, scalar) for v in value))
    if not ok or len(json.dumps(value, ensure_ascii=False)) > MAX_VALUE_CHARS:
        raise SettingsError("a value is a string, a number, a boolean or a list of them (at most 2000 characters)")


def _write(data: Dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)
    with _LOCK:
        _CACHE["key"] = None


def set_value(
    key: str, value: Any, *, by: str, reason: str, tenant_id: Optional[str] = None,
    expected_rev: Optional[int] = None, now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Set `key` (globally, or for one tenant). A value of None removes it, so the default applies again.

    Needs a reason and an author. With `expected_rev`, a change made by someone else meanwhile is a Conflict."""
    if not KEY_RE.fullmatch(str(key or "")):
        raise SettingsError("the key is lower-case letters, digits, '_', '.' or '-' (at most 64 characters)")
    if tenant_id is not None and not TENANT_RE.fullmatch(str(tenant_id)):
        raise SettingsError("the tenant id is not valid")
    reason, by = str(reason or "").strip(), str(by or "").strip()
    if not reason or len(reason) > 300:
        raise SettingsError("a reason (1-300 characters) is required")
    if not by or len(by) > 100:
        raise SettingsError("the author (1-100 characters) is required")
    _check_value(value)
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with audit_log._FileLock(path):
        data = load()
        if expected_rev is not None and int(expected_rev) != int(data["rev"]):
            raise Conflict("the settings were changed by someone else; reload and try again")
        bucket = data["tenants"].setdefault(str(tenant_id), {}) if tenant_id else data["global"]
        before = (bucket.get(key) or {}).get("value")
        data["rev"] = int(data["rev"]) + 1
        if value is None:
            bucket.pop(key, None)
            if tenant_id and not bucket:
                data["tenants"].pop(str(tenant_id), None)
        else:
            bucket[key] = {"value": value, "by": by, "reason": reason, "at": stamp, "rev": data["rev"]}
        record = {"rev": data["rev"], "at": stamp, "key": key, "tenant_id": tenant_id, "by": by, "reason": reason,
                  "before": before, "after": value}
        data["log"] = (data["log"] + [record])[-MAX_LOG:]
        _write(data)
    audit_log.append_audit_event("runtime_settings", {"action": "setting_changed", **record})
    return record


def history(limit: int = 50) -> List[Dict[str, Any]]:
    """The latest changes, newest first (the log kept in the store; the audit log holds the whole chain)."""
    return list(reversed(load()["log"]))[: max(1, int(limit))]
