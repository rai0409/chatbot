"""Cache for model loaders, with a lock for the first load (stage 14).

lru_cache does not stop two first requests from loading the same model at
the same time (twice the memory and start-up time); the lock makes the second
caller wait for the first load. 2026-11-22: a cache hit no longer takes the
lock (every query embedding used to serialize on it); only a miss does.
The cache key is the full argument tuple, so callers must pass everything
that changes the loaded model (name, revision, max length, ...).
cache_clear() and cache_info() are kept; peek(*args) reads without loading.
"""
from __future__ import annotations

import functools
import threading
from collections import OrderedDict
from typing import Any, Callable, NamedTuple


class _CacheInfo(NamedTuple):
    hits: int
    misses: int
    maxsize: int
    currsize: int


def load_once(maxsize: int) -> Callable:
    def decorate(fn: Callable) -> Callable:
        cache: "OrderedDict[tuple, Any]" = OrderedDict()
        lock = threading.Lock()
        stats = {"hits": 0, "misses": 0}

        @functools.wraps(fn)
        def wrapper(*args):
            key = tuple(args)
            # Lock-free fast path: a dict lookup is atomic in CPython.
            try:
                value = cache[key]
            except KeyError:
                pass
            else:
                stats["hits"] += 1
                return value
            with lock:
                if key in cache:
                    stats["hits"] += 1
                    return cache[key]
                stats["misses"] += 1
                value = fn(*args)
                cache[key] = value
                while maxsize and len(cache) > maxsize:
                    cache.popitem(last=False)
                return value

        def cache_clear() -> None:
            with lock:
                cache.clear()
                stats["hits"] = stats["misses"] = 0

        def cache_info() -> _CacheInfo:
            return _CacheInfo(stats["hits"], stats["misses"], maxsize, len(cache))

        def peek(*args):
            """The cached value for these arguments, or None (never loads)."""
            return cache.get(tuple(args))

        wrapper.peek = peek  # type: ignore[attr-defined]
        wrapper.cache_clear = cache_clear  # type: ignore[attr-defined]
        wrapper.cache_info = cache_info  # type: ignore[attr-defined]
        return wrapper

    return decorate
