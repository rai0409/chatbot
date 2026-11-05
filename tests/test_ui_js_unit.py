"""Stage 13: the screen scripts' unit tests (tests/js/*.test.js) run with
node --test. Skipped when Node.js is not installed; CI installs it.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS_TESTS = sorted((ROOT / "tests" / "js").glob("*.test.js"))


def test_js_tests_exist():
    names = {p.name for p in JS_TESTS}
    assert {"render.test.js", "login.test.js"} <= names


def test_screen_scripts_pass_node_test():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    # Files are passed one by one: newer Node does not accept a directory.
    proc = subprocess.run(
        [node, "--test", *[str(p) for p in JS_TESTS]],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
