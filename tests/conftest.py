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
