"""A circuit breaker for the LLM endpoints (S2, shared between workers in S7).

After `threshold` failures in a row the breaker opens: calls are refused at once, without waiting for a timeout, so a
stopped endpoint costs the user nothing but the switch to the extractive answer. After `open_seconds` it lets `probes`
trial calls through (half open); a success closes it, a failure opens it again for another period.

The state lives in a store. A store offers `update(key, default, fn)`: it reads the state of `key` (or `default`), calls
`fn(state) -> (new_state, result)`, keeps `new_state` and returns `result`, as one step that no other worker can
interleave with. The default store is in the process (one worker learns alone). The web layer can set a store that
several workers share (Redis, webapi/breaker_store.py): then every worker sees the same state, and the trial calls of a
half-open breaker are counted once for all of them. A store that cannot be reached is replaced, for that call, by the
store in the process (`FallbackStore`): a breaker that cannot be shared still protects the worker it runs in.
Every change of state is passed to the registered listeners (the usage log keeps them, so the time spent in each state
can be added up later).
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"

State = Dict[str, Any]


def default_state() -> State:
    return {"state": CLOSED, "failures": 0, "opened_at": 0.0, "probes": 0}


class Store(Protocol):
    def update(self, key: str, default: State, fn: Callable[[State], Tuple[State, Any]]) -> Any: ...


class MemoryStore:
    """The state in this process. `load` and `save` are kept for callers that only read or write."""

    def __init__(self) -> None:
        self._data: Dict[str, State] = {}
        self._lock = threading.Lock()

    def load(self, key: str) -> Optional[State]:
        with self._lock:
            found = self._data.get(key)
            return dict(found) if found else None

    def save(self, key: str, state: State) -> None:
        with self._lock:
            self._data[key] = dict(state)

    def update(self, key: str, default: State, fn: Callable[[State], Tuple[State, Any]]) -> Any:
        with self._lock:
            current = dict(self._data.get(key) or default)
            new_state, result = fn(current)
            self._data[key] = dict(new_state)
            return result


class FallbackStore:
    """Use `primary` (a store shared between workers); when it fails, use `fallback` (this process) for that call.

    `on_error(exc)` is told about each failure (to log it, rate limited by the caller). The primary is tried again on the next call,
    so a Redis that comes back is used again without a restart."""

    def __init__(self, primary: Store, fallback: Store, on_error: Optional[Callable[[BaseException], None]] = None):
        self.primary, self.fallback, self.on_error = primary, fallback, on_error
        self.fell_back = 0

    def load(self, key: str) -> Optional[State]:
        try:
            return self.primary.load(key)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            self.fell_back += 1
            if self.on_error is not None:
                try:
                    self.on_error(exc)
                except Exception:  # noqa: BLE001
                    pass
            return self.fallback.load(key)  # type: ignore[attr-defined]

    def update(self, key: str, default: State, fn: Callable[[State], Tuple[State, Any]]) -> Any:
        try:
            return self.primary.update(key, default, fn)
        except Exception as exc:  # noqa: BLE001 - any failure of the shared store: keep protecting this worker
            self.fell_back += 1
            if self.on_error is not None:
                try:
                    self.on_error(exc)
                except Exception:  # noqa: BLE001
                    pass
            return self.fallback.update(key, default, fn)


_LOCK = threading.Lock()
_STORE: Store = MemoryStore()
_BREAKERS: Dict[str, "CircuitBreaker"] = {}
_LISTENERS: List[Callable[[str, str, str, float], None]] = []


def set_store(store: Optional[Store]) -> None:
    """Use `store` for every breaker from now on (None: back to the in-process store)."""
    global _STORE
    with _LOCK:
        _STORE = store if store is not None else MemoryStore()
        _BREAKERS.clear()


def current_store() -> Store:
    return _STORE


def add_listener(listener: Callable[[str, str, str, float], None]) -> None:
    """listener(key, old_state, new_state, at) is called on every change of state."""
    if listener not in _LISTENERS:
        _LISTENERS.append(listener)


def reset_all() -> None:
    """Forget every breaker and listener (tests, and a clean start)."""
    global _STORE
    with _LOCK:
        _STORE = MemoryStore()
        _BREAKERS.clear()
        _LISTENERS.clear()


class CircuitBreaker:
    def __init__(self, key: str, *, threshold: int = 3, open_seconds: float = 60.0, probes: int = 1,
                 store: Optional[Store] = None, clock: Callable[[], float] = time.time):
        self.key = key
        self.threshold = max(1, int(threshold))
        self.open_seconds = max(0.0, float(open_seconds))
        self.probes = max(1, int(probes))
        self._store = store
        self._clock = clock

    def _backend(self) -> Store:
        return self._store if self._store is not None else _STORE

    def _step(self, fn: Callable[[State], Tuple[State, Any]]) -> Tuple[Any, str, str]:
        """Run one atomic change of the state; return (its result, the state before, the state after) and tell the listeners."""
        seen: Dict[str, str] = {}

        def wrapped(current: State) -> Tuple[State, Any]:
            seen["old"] = current["state"]
            new_state, result = fn(current)
            seen["new"] = new_state["state"]
            return new_state, result

        result = self._backend().update(self.key, default_state(), wrapped)
        if seen["old"] != seen["new"]:
            for listener in list(_LISTENERS):
                try:
                    listener(self.key, seen["old"], seen["new"], self._clock())
                except Exception:  # noqa: BLE001 - a listener never breaks a call
                    pass
        return result, seen["old"], seen["new"]

    def _peek(self) -> State:
        """The stored state without changing it (a store that cannot only read is asked through `update`)."""
        store = self._backend()
        loader = getattr(store, "load", None)
        if loader is not None:
            found = loader(self.key)
            return {**default_state(), **found} if isinstance(found, dict) else default_state()
        return store.update(self.key, default_state(), lambda current: (current, dict(current)))

    def state(self) -> str:
        """The state now (an open breaker whose period is over reads as half open). Reads only."""
        current = self._peek()
        if current["state"] == OPEN and self._clock() - float(current["opened_at"]) >= self.open_seconds:
            return HALF_OPEN
        return current["state"]

    def allow(self) -> bool:
        """May a call be made now? In the half-open state only `probes` trial calls are let through."""
        def step(current: State) -> Tuple[State, bool]:
            old = current["state"]
            if old == CLOSED:
                return current, True
            if old == OPEN:
                if self._clock() - float(current["opened_at"]) < self.open_seconds:
                    return current, False
                current.update(state=HALF_OPEN, probes=0)
            if current["probes"] >= self.probes:
                return current, False
            current["probes"] += 1
            return current, True

        allowed, _old, _new = self._step(step)
        return bool(allowed)

    def record_success(self) -> None:
        quiet = self._peek()
        if quiet["state"] == CLOSED and not quiet["failures"]:
            return  # the usual case: nothing to record (and no write to a shared store on every call)

        def step(current: State) -> Tuple[State, None]:
            current.update(state=CLOSED, failures=0, opened_at=0.0, probes=0)
            return current, None

        self._step(step)

    def record_failure(self) -> None:
        def step(current: State) -> Tuple[State, None]:
            if current["state"] in (HALF_OPEN, OPEN):
                current.update(state=OPEN, opened_at=self._clock(), probes=0)
            else:
                current["failures"] += 1
                if current["failures"] >= self.threshold:
                    current.update(state=OPEN, opened_at=self._clock(), probes=0)
            return current, None

        self._step(step)


def get(key: str, *, threshold: int, open_seconds: float, probes: int) -> CircuitBreaker:
    """The breaker of `key` (made once; its limits are those of the first call)."""
    with _LOCK:
        found = _BREAKERS.get(key)
        if found is None:
            found = _BREAKERS[key] = CircuitBreaker(key, threshold=threshold, open_seconds=open_seconds, probes=probes)
        return found
