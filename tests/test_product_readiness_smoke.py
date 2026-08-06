from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CHECKLIST = ROOT / "docs" / "production_readiness_checklist.md"
SMOKE_SCRIPT = ROOT / "scripts" / "product_readiness_smoke.sh"


def test_checklist_file_exists_and_mentions_key_blockers():
    text = CHECKLIST.read_text(encoding="utf-8")

    assert "Known Production Blockers" in text
    assert "Tenant/customer runtime selection" in text
    assert "Knowledge manifest and source versioning" in text
    assert "Citation/source metadata hardening" in text
    assert "DB persistence and tenant isolation" in text
    assert "Rollback/profile promotion workflow" in text


def test_checklist_mentions_required_safety_areas():
    text = CHECKLIST.read_text(encoding="utf-8")

    assert "Admin Auth" in text
    assert "Approved similar non-exact matches remain candidate-only" in text
    assert "Product Profiles" in text
    assert "Audit And Feedback Safety" in text
    assert "production_safe" in text
    assert "/chat/product-preview" in text


def test_smoke_script_exists_with_bash_shebang():
    text = SMOKE_SCRIPT.read_text(encoding="utf-8")

    assert text.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in text


def test_smoke_script_includes_expected_pytest_targets():
    text = SMOKE_SCRIPT.read_text(encoding="utf-8")

    for target in (
        "tests/test_admin_auth.py",
        "tests/test_review_queue_page.py",
        "tests/test_review_actions.py",
        "tests/test_product_profile.py",
        "tests/test_product_route_policy.py",
        "tests/test_production_readiness_report.py",
        "tests/test_product_preview_profiles.py",
        "tests/test_product_preview_chat.py",
        "tests/test_product_preview_feedback_rerank.py",
        "tests/test_product_preview_feature_rerank.py",
    ):
        assert target in text


def test_smoke_script_includes_py_compile_targets():
    text = SMOKE_SCRIPT.read_text(encoding="utf-8")

    for target in (
        "webapi/main.py",
        "webapi/admin_auth.py",
        "eval/production_readiness_report.py",
        "rag_core/product_profile.py",
        "rag_core/product_route_policy.py",
    ):
        assert target in text
    assert "-m py_compile" in text


def test_smoke_script_does_not_start_server_or_require_running_server():
    text = SMOKE_SCRIPT.read_text(encoding="utf-8")

    assert '"$PYTHON" -m uvicorn' not in text
    assert "This script does not start uvicorn" in text


def _fake_python(tmp_path: Path) -> tuple[Path, Path]:
    log = tmp_path / "calls.log"
    executable = tmp_path / "fake-python"
    executable.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> \"$FAKE_PYTHON_LOG\"\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable, log


def _run_smoke(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SMOKE_SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        env=env,
    )


def test_smoke_script_uses_explicit_interpreter_for_pytest_and_compile(tmp_path):
    executable, log = _fake_python(tmp_path)
    env = {**os.environ, "FAKE_PYTHON_LOG": str(log)}

    result = _run_smoke("--python", str(executable), env=env)

    assert result.returncode == 0, result.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 2
    assert calls[0].startswith("-m pytest ")
    assert calls[1].startswith("-m py_compile ")


def test_explicit_interpreter_takes_priority_over_environment(tmp_path):
    explicit, log = _fake_python(tmp_path)
    environment = tmp_path / "environment-python"
    environment.write_text("#!/usr/bin/env bash\nexit 99\n", encoding="utf-8")
    environment.chmod(0o755)
    env = {
        **os.environ,
        "FAKE_PYTHON_LOG": str(log),
        "PRODUCT_READINESS_PYTHON": str(environment),
    }

    result = _run_smoke("--python", str(explicit), env=env)

    assert result.returncode == 0, result.stderr
    assert len(log.read_text(encoding="utf-8").splitlines()) == 2


def test_environment_interpreter_is_used_when_explicit_is_absent(tmp_path):
    executable, log = _fake_python(tmp_path)
    env = {**os.environ, "FAKE_PYTHON_LOG": str(log), "PRODUCT_READINESS_PYTHON": str(executable)}

    result = _run_smoke(env=env)

    assert result.returncode == 0, result.stderr
    assert len(log.read_text(encoding="utf-8").splitlines()) == 2


@pytest.mark.parametrize(
    ("args", "environment"),
    [
        (("--python", "/missing/python"), {}),
        ((), {"PRODUCT_READINESS_PYTHON": "/missing/python"}),
        (("--unknown",), {}),
        (("--python",), {}),
        (("unexpected",), {}),
    ],
)
def test_smoke_script_rejects_invalid_cli_and_interpreters(args, environment):
    result = _run_smoke(*args, env={**os.environ, **environment})

    assert result.returncode != 0


def test_smoke_script_help_and_portable_manual_example():
    result = _run_smoke("--help")
    text = SMOKE_SCRIPT.read_text(encoding="utf-8")

    assert result.returncode == 0
    assert "--python <executable>" in result.stdout
    assert ".venv/bin/python" not in text
    assert "${PRODUCT_READINESS_PYTHON:-python3}" in text


def test_smoke_script_does_not_reference_pr43_files():
    text = SMOKE_SCRIPT.read_text(encoding="utf-8")

    assert "feature_rerank_promotion_gate" not in text
