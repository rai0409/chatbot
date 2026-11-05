"""Stage 13: no pyflakes finding (unused imports / variables, undefined
names) in the repository's Python code. Skipped when pyflakes is not
installed; CI installs it and also runs it directly.
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ["rag_core", "webapi", "scripts", "eval", "tools", "tests", "libs", "config.py", "rag_grounded.py"]


def test_pyflakes_has_no_finding():
    api = pytest.importorskip("pyflakes.api")
    reporter_mod = pytest.importorskip("pyflakes.reporter")
    out, err = io.StringIO(), io.StringIO()
    reporter = reporter_mod.Reporter(out, err)
    paths = [str(ROOT / t) for t in TARGETS if (ROOT / t).exists()]
    count = api.checkRecursive(paths, reporter)
    assert count == 0, out.getvalue() + err.getvalue()
