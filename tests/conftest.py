"""Shared fixtures: the LLM gateway keeps state (breakers, a parsed profiles file), so every test starts clean.

This fixture does not ask for `monkeypatch`: a fixture that does is set up before the test's own fixtures and torn down
after them, which changes the order in which other tests' patches are undone."""
import pytest

import config


def _reset() -> None:
    from rag_core import circuit_breaker, llm_gateway, llm_profiles, llm_quota

    llm_quota.reset()
    circuit_breaker.reset_all()
    llm_gateway.set_metrics_hook(None)
    llm_profiles._CACHE.update(key=None, value=None)


@pytest.fixture(autouse=True)
def _clean_llm_gateway_state():
    _reset()
    before = getattr(config, "LLM_USAGE_ENABLED", True)
    config.LLM_USAGE_ENABLED = False  # the tests of the usage log switch it on, in their own directory
    try:
        yield
    finally:
        config.LLM_USAGE_ENABLED = before
        _reset()


# --- optional dependencies ----------------------------------------------------------------------------------------
#
# Tests of the Anthropic adapter and of the Redis-backed breaker need packages from requirements-anthropic.txt /
# requirements-state.txt. Without them the tests are skipped (a plain checkout stays green); with
# REQUIRE_OPTIONAL_DEPS=1 (set in CI) a missing package is a failure, so a broken install cannot hide as a skip.
import importlib
import os


def _require_optional(name: str) -> None:
    try:
        importlib.import_module(name)
    except ImportError:
        if os.environ.get("REQUIRE_OPTIONAL_DEPS") == "1":
            pytest.fail(f"{name} is required (REQUIRE_OPTIONAL_DEPS=1) but is not installed", pytrace=False)
        pytest.skip(f"{name} is not installed (see requirements-anthropic.txt / requirements-state.txt)")


@pytest.fixture
def require_anthropic():
    _require_optional("anthropic")


@pytest.fixture
def require_redis():
    _require_optional("redis")
