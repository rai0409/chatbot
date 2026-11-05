"""lru_cache for model loaders, with a lock (stage 14).

lru_cache does not stop two first requests from loading the same model at
the same time (twice the memory and start-up time); the lock makes the second
caller wait for the first load. cache_clear() and cache_info() are kept.
"""
from __future__ import annotations

import functools
import threading
from typing import Callable


def load_once(maxsize: int) -> Callable:
    def decorate(fn: Callable) -> Callable:
        cached = functools.lru_cache(maxsize=maxsize)(fn)
        lock = threading.Lock()

        @functools.wraps(fn)
        def wrapper(*args):
            with lock:
                return cached(*args)

        wrapper.cache_clear = cached.cache_clear  # type: ignore[attr-defined]
        wrapper.cache_info = cached.cache_info  # type: ignore[attr-defined]
        return wrapper

    return decorate
