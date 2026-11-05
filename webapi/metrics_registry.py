from __future__ import annotations

import atexit
import glob
import json
import os
import time
from threading import Lock
from typing import Dict, Optional


# In-process, thread-safe counters. With multiple uvicorn workers each
# process keeps its own numbers; set METRICS_MULTIPROC_DIR (stage 10) and
# every process also writes its counters to <dir>/<pid>-<start>.json (at most
# once a second, atomically) so /metrics and the admin dashboard report the
# sum over all workers of this node. Clear the directory when the service
# starts (systemd ExecStartPre / container entrypoint); files of workers that
# exited are kept on purpose so totals never go backwards while it runs.
_LOCK = Lock()
_COUNTERS: Dict[str, Dict[str, int]] = {}

_MAX_LABEL_CHARS = 120
_PROCESS_TAG = f"{os.getpid()}-{int(time.time() * 1000)}"
_FLUSH_STATE = {"last": 0.0, "dirty": False}
# Requests / errored requests handled by this process (main.py calls
# note_request / note_error); written with the counters, summed by
# aggregate_totals().
_TOTALS = {"requests": 0, "errors": 0}


def _multiproc_dir() -> str:
    return str(os.getenv("METRICS_MULTIPROC_DIR", "") or "").strip()


def scope() -> str:
    return "all_workers" if _multiproc_dir() else "this_process"


def increment(name: str, label: Optional[str] = None, amount: int = 1) -> None:
    # Labels must be stable enum-like values; never raw queries or secrets.
    key = str(label)[:_MAX_LABEL_CHARS] if label is not None else "total"
    with _LOCK:
        bucket = _COUNTERS.setdefault(str(name), {})
        bucket[key] = bucket.get(key, 0) + int(amount)
        _FLUSH_STATE["dirty"] = True
    _flush()


def note_request() -> None:
    with _LOCK:
        _TOTALS["requests"] += 1
        _FLUSH_STATE["dirty"] = True
    _flush()


def note_error() -> None:
    with _LOCK:
        _TOTALS["errors"] += 1
        _FLUSH_STATE["dirty"] = True
    _flush()


def snapshot() -> Dict[str, Dict[str, int]]:
    with _LOCK:
        return {name: dict(bucket) for name, bucket in _COUNTERS.items()}


def reset() -> None:
    with _LOCK:
        _COUNTERS.clear()
        _FLUSH_STATE["dirty"] = True
    _flush(force=True)


def _flush(force: bool = False) -> None:
    directory = _multiproc_dir()
    if not directory:
        return
    now = time.monotonic()
    if not force and (not _FLUSH_STATE["dirty"] or now - _FLUSH_STATE["last"] < 1.0):
        return
    data = snapshot()
    with _LOCK:
        totals = dict(_TOTALS)
    try:
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f"{_PROCESS_TAG}.json")
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "counters": data, "totals": totals}, f)
        os.replace(tmp, path)
        _FLUSH_STATE["last"] = now
        _FLUSH_STATE["dirty"] = False
    except OSError:
        pass  # metrics must never break a request


def aggregate_snapshot() -> Dict[str, Dict[str, int]]:
    """Counters summed over every worker (this process only without the dir)."""
    directory = _multiproc_dir()
    if not directory:
        return snapshot()
    _flush(force=True)
    total: Dict[str, Dict[str, int]] = {}
    for path in glob.glob(os.path.join(directory, "*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                counters = json.load(f).get("counters") or {}
        except (OSError, ValueError):
            continue
        for name, bucket in counters.items():
            if not isinstance(bucket, dict):
                continue
            out = total.setdefault(str(name), {})
            for label, value in bucket.items():
                try:
                    out[str(label)] = out.get(str(label), 0) + int(value)
                except (TypeError, ValueError):
                    continue
    return total


def aggregate_totals() -> Dict[str, int]:
    """{"requests", "errors"} summed over every worker (this process only without the dir)."""
    directory = _multiproc_dir()
    with _LOCK:
        own = dict(_TOTALS)
    if not directory:
        return own
    _flush(force=True)
    total = {"requests": 0, "errors": 0}
    for path in glob.glob(os.path.join(directory, "*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                values = json.load(f).get("totals") or {}
        except (OSError, ValueError):
            continue
        for key in total:
            try:
                total[key] += int(values.get(key, 0))
            except (TypeError, ValueError):
                continue
    return total


atexit.register(lambda: _flush(force=True))


# Prometheus text exposition format (version 0.0.4). The content type below is
# the one Prometheus scrapers expect for this format.
PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _escape_label_value(value: str) -> str:
    # Per the exposition format, backslash, double-quote, and newline must be
    # escaped in label values. Label keys here are stable enum-like strings,
    # never raw queries or secrets, but we escape defensively regardless.
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def to_prometheus(payload: Dict[str, object]) -> str:
    # Render the /metrics JSON payload as Prometheus text exposition. Without
    # METRICS_MULTIPROC_DIR the counters are per-process: with multiple
    # uvicorn workers each scrape sees only one worker; aggregate externally.
    note = (
        "# NOTE counters are summed over the workers of this node (METRICS_MULTIPROC_DIR)."
        if payload.get("counters_scope") == "all_workers"
        else "# NOTE counters are per-process; aggregate across workers externally."
    )
    lines = [
        note,
        "# HELP app_uptime_seconds Process uptime in seconds.",
        "# TYPE app_uptime_seconds gauge",
        f"app_uptime_seconds {int(payload.get('uptime_seconds', 0) or 0)}",
        "# HELP app_requests_total Requests handled (all workers with METRICS_MULTIPROC_DIR).",
        "# TYPE app_requests_total counter",
        f"app_requests_total {int(payload.get('total_requests', 0) or 0)}",
        "# HELP app_error_requests_total Errored requests (all workers with METRICS_MULTIPROC_DIR).",
        "# TYPE app_error_requests_total counter",
        f"app_error_requests_total {int(payload.get('error_requests', 0) or 0)}",
    ]
    counters = payload.get("counters") or {}
    if isinstance(counters, dict):
        for name in sorted(counters):
            buckets = counters[name]
            if not isinstance(buckets, dict):
                continue
            lines.append(f"# TYPE {name} counter")
            for label_key in sorted(buckets):
                value = int(buckets[label_key])
                if label_key == "total":
                    lines.append(f"{name} {value}")
                else:
                    safe = _escape_label_value(label_key)
                    lines.append(f'{name}{{label="{safe}"}} {value}')
    return "\n".join(lines) + "\n"
