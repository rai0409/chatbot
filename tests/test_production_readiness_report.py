from __future__ import annotations

import json

from eval.production_readiness_report import build_report, render_markdown, write_report


def test_report_generator_creates_json_and_markdown_in_tmp_output_dir(tmp_path):
    report = build_report()

    json_path, md_path = write_report(report, tmp_path)

    assert json_path.exists()
    assert md_path.exists()
    loaded = json.loads(json_path.read_text(encoding="utf-8"))
    assert loaded["readiness_decision"]["decision"] == report["readiness_decision"]["decision"]
    assert "Production Readiness Report" in md_path.read_text(encoding="utf-8")


def test_json_includes_required_top_level_sections():
    report = build_report()

    for key in (
        "readiness_decision",
        "safety_checks",
        "product_profiles",
        "tenant_profiles",
        "known_blockers",
        "recommended_next_steps",
    ):
        assert key in report


def test_production_safe_keeps_similar_auto_answer_and_llm_disabled():
    report = build_report()
    production_safe = report["product_profiles"]["production_safe"]

    assert production_safe["allow_similar_auto_answer"] is False
    assert production_safe["llm_answer"] is False
    assert production_safe["llm_rerank"] is False
    assert report["safety_checks"]["similar_auto_answer_disabled"] is True


def test_report_never_returns_ready_for_full_production():
    report = build_report()

    assert report["readiness_decision"]["decision"] != "ready_for_full_production"


def test_missing_optional_artifacts_warn_but_do_not_crash():
    report = build_report()

    assert "decision_artifact_present" in report["rerank_promotion"]
    assert "generated_manifest_present" in report["knowledge_manifest"]
    assert isinstance(report["rerank_promotion"]["warnings"], list)
    assert isinstance(report["knowledge_manifest"]["warnings"], list)


def test_markdown_includes_top_level_decision_and_blockers():
    report = build_report()
    markdown = render_markdown(report)

    assert "## Decision:" in markdown
    assert "## Known Blockers" in markdown
    assert "DB persistence / tenant isolation" in markdown


def test_report_does_not_start_uvicorn_or_require_server():
    report = build_report()
    raw = json.dumps(report, ensure_ascii=False)

    assert "uvicorn" not in raw
    assert "127.0.0.1:8000" not in raw


def test_report_does_not_require_network_access_or_include_private_file_contents():
    report = build_report()
    raw = json.dumps(report, ensure_ascii=False)

    assert "http://" not in raw
    assert "https://" not in raw
    assert "秘密の本文" not in raw
    assert "private file content" not in raw


def test_report_includes_admin_auth_guidance_and_env_vars():
    report = build_report()
    admin = report["admin_auth"]

    assert admin["helper_present"] is True
    assert "ADMIN_AUTH_ENABLED" in admin["env_vars"]
    assert "ADMIN_AUTH_TOKEN" in admin["env_vars"]
    assert "ADMIN_AUTH_ENABLED=true" in admin["production_guidance"]


def test_report_includes_tenant_mapping_summary():
    report = build_report()
    tenant = report["tenant_profiles"]

    assert tenant["mapping_present"] is True
    assert tenant["default_profile"] == "production_safe"
    assert tenant["tenant_count"] >= 1


def test_report_includes_readiness_smoke_script_reference():
    report = build_report()

    assert report["safety_checks"]["readiness_smoke_script_present"] is True
    assert "run scripts/product_readiness_smoke.sh" in report["recommended_next_steps"]


def test_report_is_deterministic_enough_for_tests(tmp_path):
    report_a = build_report()
    report_b = build_report()
    report_a.pop("generated_at", None)
    report_b.pop("generated_at", None)

    assert report_a["safety_checks"] == report_b["safety_checks"]
    assert report_a["product_profiles"] == report_b["product_profiles"]

    json_path, _md_path = write_report(build_report(), tmp_path)
    loaded = json.loads(json_path.read_text(encoding="utf-8"))
    assert sorted(loaded.keys()) == sorted(build_report().keys())
