from __future__ import annotations

from pathlib import Path


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


def test_smoke_script_does_not_reference_pr43_files():
    text = SMOKE_SCRIPT.read_text(encoding="utf-8")

    assert "feature_rerank_promotion_gate" not in text
