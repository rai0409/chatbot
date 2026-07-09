from __future__ import annotations

from threading import Lock
from typing import Dict, Optional


# In-process, thread-safe counters. With multiple uvicorn workers each
# process reports its own numbers; they are not globally aggregated.
_LOCK = Lock()
_COUNTERS: Dict[str, Dict[str, int]] = {}

_MAX_LABEL_CHARS = 120


def increment(name: str, label: Optional[str] = None, amount: int = 1) -> None:
    # Labels must be stable enum-like values; never raw queries or secrets.
    key = str(label)[:_MAX_LABEL_CHARS] if label is not None else "total"
    with _LOCK:
        bucket = _COUNTERS.setdefault(str(name), {})
        bucket[key] = bucket.get(key, 0) + int(amount)


def snapshot() -> Dict[str, Dict[str, int]]:
    with _LOCK:
        return {name: dict(bucket) for name, bucket in _COUNTERS.items()}


def reset() -> None:
    with _LOCK:
        _COUNTERS.clear()


# Prometheus text exposition format (version 0.0.4). The content type below is
# the one Prometheus scrapers expect for this format.
PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _escape_label_value(value: str) -> str:
    # Per the exposition format, backslash, double-quote, and newline must be
    # escaped in label values. Label keys here are stable enum-like strings,
    # never raw queries or secrets, but we escape defensively regardless.
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def to_prometheus(payload: Dict[str, object]) -> str:
    # Render the /metrics JSON payload as Prometheus text exposition. Counters
    # are per-process (same caveat as the JSON output): with multiple uvicorn
    # workers each process exposes only its own numbers; aggregate externally.
    lines = [
        "# NOTE counters are per-process; aggregate across workers externally.",
        "# HELP app_uptime_seconds Process uptime in seconds.",
        "# TYPE app_uptime_seconds gauge",
        f"app_uptime_seconds {int(payload.get('uptime_seconds', 0) or 0)}",
        "# HELP app_requests_total Requests handled by this process.",
        "# TYPE app_requests_total counter",
        f"app_requests_total {int(payload.get('total_requests', 0) or 0)}",
        "# HELP app_error_requests_total Errored requests in this process.",
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
