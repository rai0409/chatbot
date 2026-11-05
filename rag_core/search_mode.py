"""Per-request search mode: "deep" means the user asked for a more thorough look at this question.

A ContextVar, so nothing has to be threaded through the answer functions. Set it around the call that does the
retrieval (deep_mode) or around the iteration of a generator that does (iterate_in_mode).
"""
from __future__ import annotations

import contextlib
from contextvars import ContextVar
from typing import Iterable, Iterator, TypeVar

T = TypeVar("T")

_DEEP: ContextVar[bool] = ContextVar("deep_search", default=False)


def is_deep() -> bool:
    return _DEEP.get()


@contextlib.contextmanager
def deep_mode(on: bool = True) -> Iterator[None]:
    token = _DEEP.set(bool(on))
    try:
        yield
    finally:
        _DEEP.reset(token)


def iterate_in_mode(deep: bool, iterator: Iterable[T]) -> Iterator[T]:
    """Yield from `iterator`, with the mode set while the iterator runs (not while the caller handles an item)."""
    source = iter(iterator)
    while True:
        with deep_mode(deep):
            try:
                item = next(source)
            except StopIteration:
                return
        yield item
