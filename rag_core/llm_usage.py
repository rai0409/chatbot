"""What was asked of the LLM, by whom, with which model, and what it cost (S2).

One line of JSON per call in a file per day (LLM_USAGE_DIR, default <RUNS_DIR>/usage/llm_usage-YYYY-MM-DD.jsonl). A
line holds no question, no answer and no document text: only the tenant, who asked (the fingerprint of the API key and the
role of the caller, never a name), the feature, the profile, the model, the host, the tokens, the time and the result.
Breaker changes of state go to the same file as events of their own, so the time spent in each state can be added up.

Tokens come from the provider's answer when it reports them. Where it does not (a stream, a local server that leaves
them out), they are estimated from the text length and the line says so (`estimated: true`).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

import config
from rag_core import audit_log, availability

OUTCOMES = ("ok", "error", "refused", "breaker_open")

logger = logging.getLogger(__name__)

# Failed usage writes (disk full, permissions). Counted for /metrics; while the latest write of a call failed (and
# for LLM_USAGE_FAILURE_HOLD_SECONDS after it), recording_healthy() is False and llm_quota.check fails closed when
# a limit is configured: the use that the limits are computed from is not being recorded.
_HEALTH_LOCK = threading.Lock()
_HEALTH: Dict[str, Any] = {"failures": 0, "last_failure": None, "last_success": None}
_DEFAULT_FAILURE_HOLD_SECONDS = 60.0


def _dir() -> Path:
    configured = str(getattr(config, "LLM_USAGE_DIR", "") or "").strip()
    return Path(configured) if configured else Path(config.RUNS_DIR) / "usage"


def enabled() -> bool:
    return bool(getattr(config, "LLM_USAGE_ENABLED", True))


def _file(day: date) -> Path:
    return _dir() / f"llm_usage-{day.isoformat()}.jsonl"


def estimate_tokens(text: str) -> int:
    """A rough count when the provider gives none: about one token per two characters of Japanese or four of English."""
    text = str(text or "")
    wide = sum(1 for ch in text if ord(ch) > 0x2E80)
    return max(0, wide // 2 + (len(text) - wide) // 4 + (1 if text else 0))


def _append(payload: Dict[str, Any], now: datetime) -> None:
    path = _file(now.date())
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":")) + "\n"
    with audit_log._FileLock(path):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)


def write_failure_count() -> int:
    """Usage lines that could not be written since the process started."""
    return int(_HEALTH["failures"])


def _failure_hold_seconds() -> float:
    try:
        return max(0.0, float(getattr(config, "LLM_USAGE_FAILURE_HOLD_SECONDS", _DEFAULT_FAILURE_HOLD_SECONDS)))
    except (TypeError, ValueError):
        return _DEFAULT_FAILURE_HOLD_SECONDS


def recording_healthy(*, now: Optional[float] = None) -> bool:
    """False while usage recording is failing: the latest call write failed and the hold time since has not passed.

    The hold lets a later call try again (a refused call writes nothing, so it could never recover otherwise)."""
    with _HEALTH_LOCK:
        failed, succeeded = _HEALTH["last_failure"], _HEALTH["last_success"]
    if failed is None or (succeeded is not None and succeeded >= failed):
        return True
    return (time.monotonic() if now is None else now) - failed >= _failure_hold_seconds()


def _note_write(ok: bool) -> None:
    with _HEALTH_LOCK:
        if ok:
            _HEALTH["last_success"] = time.monotonic()
        else:
            _HEALTH["failures"] += 1
            _HEALTH["last_failure"] = time.monotonic()


def reset_health() -> None:
    """Forget the recorded write failures (tests)."""
    with _HEALTH_LOCK:
        _HEALTH.update(failures=0, last_failure=None, last_success=None)


def record_call(
    *, feature: str, tenant_id: str, profile: str, model: str, host: str, location: str, outcome: str,
    input_tokens: int = 0, output_tokens: int = 0, estimated: bool = False, latency_ms: int = 0,
    error_type: str = "", cost: float = 0.0, now: Optional[datetime] = None, batch: bool = False,
    actor: Optional[Mapping[str, Any]] = None,
) -> None:
    """Never raises: the usage log must not break an answer. `batch`: made through a provider's batch API (rag_core/llm_batch.py).

    `actor` (key_fingerprint, role): who asked, when it is not the caller of the current request (a batch collected later
    by someone else). Default: the current request's caller. A failed write is counted and logged (write_failure_count,
    recording_healthy)."""
    if not enabled():
        return
    stamp = now or datetime.now(timezone.utc)
    if actor is None:
        actor = audit_log._ACTOR.get() or {}
    payload = {
        "t": stamp.isoformat(), "event": "call", "tenant_id": tenant_id, "feature": feature, "profile": profile,
        "model": model, "host": host, "location": location, "outcome": outcome,
        "input_tokens": int(input_tokens), "output_tokens": int(output_tokens), "estimated": bool(estimated),
        "latency_ms": int(latency_ms), "error_type": error_type, "cost": round(float(cost), 8),
        "key_fingerprint": actor.get("key_fingerprint"), "role": actor.get("role"), "batch": bool(batch),
    }
    try:
        _append(payload, stamp)
    except Exception as exc:  # noqa: BLE001  the usage log must not break an answer; counted and logged instead
        _note_write(False)
        logger.warning("llm usage line could not be written: %s", type(exc).__name__)
    else:
        _note_write(True)


def record_breaker(key: str, old: str, new: str, at: float) -> None:
    """A breaker listener: one line for each change of state."""
    if not enabled():
        return
    stamp = datetime.fromtimestamp(at, tz=timezone.utc)
    try:
        _append({"t": stamp.isoformat(), "event": "breaker", "key": key, "from": old, "to": new}, stamp)
    except Exception as exc:  # noqa: BLE001  a breaker listener must not raise; counted and logged instead
        with _HEALTH_LOCK:
            _HEALTH["failures"] += 1  # counted, but not a quota health signal (no call use was lost)
        logger.warning("llm breaker line could not be written: %s", type(exc).__name__)


def read(days: int = 1, *, today: Optional[date] = None) -> Iterable[Dict[str, Any]]:
    """The lines of the last `days` days (today included), oldest first."""
    last = today or datetime.now(timezone.utc).date()
    for offset in range(max(1, int(days)) - 1, -1, -1):
        path = _file(last - timedelta(days=offset))
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue


def summarize(days: int = 1, *, by: str = "tenant_id", today: Optional[date] = None, tenants: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
    """Calls, errors, tokens and cost added up by `by` (tenant_id, feature, profile, model, key_fingerprint, location)."""
    totals: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"calls": 0, "ok": 0, "error": 0, "refused": 0, "breaker_open": 0,
                                                              "input_tokens": 0, "output_tokens": 0, "cost": 0.0,
                                                              "latency_ms": 0})
    allowed = set(tenants) if tenants is not None else None
    for row in read(days, today=today):
        if row.get("event") != "call" or (allowed is not None and row.get("tenant_id") not in allowed):
            continue
        bucket = totals[str(row.get(by) or "")]
        bucket["calls"] += 1
        bucket[row.get("outcome") if row.get("outcome") in OUTCOMES else "error"] += 1
        for field in ("input_tokens", "output_tokens", "latency_ms"):
            bucket[field] += int(row.get(field) or 0)
        bucket["cost"] += float(row.get("cost") or 0.0)
    return [{by: key, **values, "cost": round(values["cost"], 6)} for key, values in sorted(totals.items())]


def state_seconds(days: int = 1, *, now: Optional[datetime] = None, today: Optional[date] = None) -> Dict[str, Dict[str, float]]:
    """Seconds spent in each breaker state, by breaker key, from the recorded changes (the last state runs to `now`)."""
    clock = now or datetime.now(timezone.utc)
    spent: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    last: Dict[str, tuple] = {}
    for row in read(days, today=today):
        if row.get("event") != "breaker":
            continue
        at = datetime.fromisoformat(row["t"])
        key = str(row["key"])
        if key in last:
            spent[key][last[key][0]] += (at - last[key][1]).total_seconds()
        last[key] = (row["to"], at)
    for key, (state, at) in last.items():
        spent[key][state] += max(0.0, (clock - at).total_seconds())
    return {key: dict(values) for key, values in spent.items()}


def purge(*, now: Optional[datetime] = None, dry_run: bool = True) -> Dict[str, Any]:
    """Delete the usage files older than LLM_USAGE_RETENTION_DAYS (a dry run only reports). Nothing else is touched."""
    keep = int(getattr(config, "LLM_USAGE_RETENTION_DAYS", 400))
    today = (now or datetime.now(timezone.utc)).date()
    summary: Dict[str, Any] = {"dry_run": dry_run, "retention_days": keep, "files_deleted": 0}
    directory = _dir()
    if not directory.exists():
        return summary
    for path in sorted(directory.glob("llm_usage-*.jsonl")):
        try:
            day = date.fromisoformat(path.stem[len("llm_usage-"):])
        except ValueError:
            continue
        if (today - day).days > keep:
            summary["files_deleted"] += 1
            if not dry_run:
                path.unlink()
    return summary


# --- business hours: how long was the LLM down while people were working? -----------------------------------------------------------


def business_seconds(start: datetime, end: datetime) -> float:
    """Seconds of [start, end) inside the business hours (weekdays, BUSINESS_HOURS_START to BUSINESS_HOURS_END local time).
    Holidays are not known: they count as working days."""
    return availability.business_seconds(
        start, end, utc_offset_hours=float(getattr(config, "BUSINESS_HOURS_UTC_OFFSET", 9.0)),
        first_hour=int(getattr(config, "BUSINESS_HOURS_START", 8)), last_hour=int(getattr(config, "BUSINESS_HOURS_END", 21)),
    )


def downtime_report(days: int = 30, *, now: Optional[datetime] = None, today: Optional[date] = None) -> Dict[str, Any]:
    """The time each LLM breaker was open (the LLM could not be used), and how much of it fell in business hours.

    While a breaker is open the answers are extractive: by the definition of the service level that is not a stop, so this is
    reported as the time of degraded answers, not as downtime of the service."""
    clock = now or datetime.now(timezone.utc)
    window_start = clock - timedelta(days=max(1, int(days)))
    open_since: Dict[str, datetime] = {}
    totals: Dict[str, Dict[str, float]] = defaultdict(lambda: {"open_seconds": 0.0, "open_business_seconds": 0.0, "opened": 0})

    def close(key: str, at: datetime) -> None:
        began = max(open_since.pop(key), window_start)
        if at > began:
            totals[key]["open_seconds"] += (at - began).total_seconds()
            totals[key]["open_business_seconds"] += business_seconds(began, at)

    for row in read(days + 1, today=today):
        if row.get("event") != "breaker":
            continue
        at = datetime.fromisoformat(row["t"])
        key = str(row["key"])
        if row.get("to") == "open":
            open_since.setdefault(key, at)
            totals[key]["opened"] += 1 if at >= window_start else 0
        elif key in open_since:
            close(key, at)
    for key in list(open_since):
        close(key, clock)
    return {"days": int(days), "window_business_seconds": business_seconds(window_start, clock),
            "by_breaker": {k: {**v, "opened": int(v["opened"])} for k, v in sorted(totals.items())}}


def daily(days: int = 30, *, tenants: Optional[Iterable[str]] = None, today: Optional[date] = None) -> List[Dict[str, Any]]:
    """Calls, errors, tokens and cost for each day."""
    allowed = set(tenants) if tenants is not None else None
    per_day: Dict[str, Dict[str, Any]] = {}
    for row in read(days, today=today):
        if row.get("event") != "call" or (allowed is not None and row.get("tenant_id") not in allowed):
            continue
        bucket = per_day.setdefault(str(row["t"])[:10], {"calls": 0, "errors": 0, "input_tokens": 0, "output_tokens": 0, "cost": 0.0})
        bucket["calls"] += 1
        bucket["errors"] += 1 if row.get("outcome") in ("error", "breaker_open") else 0
        bucket["input_tokens"] += int(row.get("input_tokens") or 0)
        bucket["output_tokens"] += int(row.get("output_tokens") or 0)
        bucket["cost"] += float(row.get("cost") or 0.0)
    return [{"day": day, **{**v, "cost": round(v["cost"], 6)}} for day, v in sorted(per_day.items())]
