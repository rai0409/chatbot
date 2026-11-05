"""run_isolated: a parser runs in its own process with a time limit (and an optional memory limit)."""
from __future__ import annotations

import os
import time

import pytest

from docextract.isolation import IsolatedFailure, run_isolated


def square(x):
    return x * x


def boom(_):
    raise ValueError("bad input")


def sleeper(_):
    time.sleep(60)


def hog(_):
    return len(b"x" * (3 * 1024 ** 3))


def hard_exit(_):
    os._exit(7)


def read_secret(name):
    return os.environ.get(name)


def test_the_result_comes_back():
    assert run_isolated(square, 7, timeout=60) == 49


def test_an_exception_is_reported_with_its_type():
    with pytest.raises(IsolatedFailure) as info:
        run_isolated(boom, 1, timeout=60)
    assert info.value.error_type == "ValueError" and "bad input" in str(info.value)


def test_a_timeout_kills_the_process_and_returns_promptly():
    start = time.time()
    with pytest.raises(IsolatedFailure) as info:
        run_isolated(sleeper, 1, timeout=1.5)
    assert info.value.error_type == "Timeout" and time.time() - start < 30


def test_a_crashing_process_is_reported():
    with pytest.raises(IsolatedFailure) as info:
        run_isolated(hard_exit, 1, timeout=60)
    assert info.value.error_type == "Crashed"


def test_the_memory_limit_turns_a_huge_allocation_into_a_failure():
    with pytest.raises(IsolatedFailure) as info:
        run_isolated(hog, 1, timeout=60, memory_mb=1024)
    assert info.value.error_type in {"MemoryError", "Crashed"}


def test_secrets_are_not_inherited(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-real")
    assert run_isolated(read_secret, "OPENAI_API_KEY", timeout=60) is None
    assert run_isolated(read_secret, "OPENAI_API_KEY", timeout=60, scrub_env=False) == "sk-not-real"
