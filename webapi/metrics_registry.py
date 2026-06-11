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
