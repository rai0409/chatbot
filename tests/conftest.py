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


# --- vector store clients -----------------------------------------------------------------------------------------
#
# rag_core.vector_backend caches Qdrant clients per process. They are closed after every test (only when the module
# was imported: no import cost for the other tests), so an embedded QDRANT_PATH lock never outlives its test and no
# client is left to the interpreter teardown (QdrantClient.__del__ during shutdown prints an ImportError).
import sys


@pytest.fixture(autouse=True)
def _close_vector_clients():
    yield
    vector_backend = sys.modules.get("rag_core.vector_backend")
    if vector_backend is not None and hasattr(vector_backend, "close_clients"):
        vector_backend.close_clients()


# --- unexpected skips (CI) ----------------------------------------------------------------------------------------
#
# With CI_FAIL_ON_UNEXPECTED_SKIP=1 the run fails when a test is skipped for a reason that tests/skip_allowlist.txt
# does not list (format: <nodeid glob> TAB <reason regex> TAB <why it is allowed>; "#" starts a comment line).
# A missing package (qdrant_client, sudachipy, pdfplumber, ...) must never be listed: it means a broken install.
import fnmatch
import re
from pathlib import Path

SKIP_ALLOWLIST_PATH = Path(__file__).resolve().parent / "skip_allowlist.txt"
_UNEXPECTED_SKIPS_KEY = pytest.StashKey[list]()


def load_skip_allowlist(path=SKIP_ALLOWLIST_PATH):
    """[(nodeid glob, compiled reason regex, why)]; a malformed line is an error, not ignored."""
    entries = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 3 or not all(p.strip() for p in parts):
            raise ValueError(f"{path}:{number}: expected <nodeid glob>\\t<reason regex>\\t<why allowed>")
        entries.append((parts[0].strip(), re.compile(parts[1].strip()), parts[2].strip()))
    return entries


def skip_reason(report) -> str:
    longrepr = report.longrepr
    reason = longrepr[2] if isinstance(longrepr, tuple) and len(longrepr) == 3 else str(longrepr or "")
    return reason[len("Skipped: "):] if reason.startswith("Skipped: ") else reason


def unexpected_skips(skips, allowlist):
    """skips: [(nodeid, reason)] -> the ones no allowlist entry matches (glob on nodeid, regex search on reason)."""
    return [
        (nodeid, reason) for nodeid, reason in skips
        if not any(fnmatch.fnmatchcase(nodeid, glob) and pattern.search(reason) for glob, pattern, _why in allowlist)
    ]


def pytest_sessionfinish(session, exitstatus):
    if os.environ.get("CI_FAIL_ON_UNEXPECTED_SKIP") != "1":
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    reports = reporter.stats.get("skipped", []) if reporter is not None else []
    skips = [(r.nodeid, skip_reason(r)) for r in reports if getattr(r, "skipped", False)]
    unexpected = unexpected_skips(skips, load_skip_allowlist())
    session.config.stash[_UNEXPECTED_SKIPS_KEY] = unexpected
    if unexpected and session.exitstatus in (pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    unexpected = config.stash.get(_UNEXPECTED_SKIPS_KEY, [])
    if not unexpected:
        return
    terminalreporter.section("unexpected skips (CI_FAIL_ON_UNEXPECTED_SKIP=1)", red=True)
    for nodeid, reason in unexpected:
        terminalreporter.write_line(f"{nodeid}: {reason}")
    terminalreporter.write_line(f"{len(unexpected)} skip(s) not in tests/skip_allowlist.txt: install the missing "
                                "dependency or, for a deliberate skip, add an entry with its justification")
