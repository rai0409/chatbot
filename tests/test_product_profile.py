from __future__ import annotations

import json

import pytest

from rag_core.product_profile import (
    get_available_product_profiles,
    load_product_profile,
    load_product_profile_from_path,
    validate_product_profile,
)


EXPECTED_PROFILES = {
    "default",
    "production_safe",
    "production_low_cost",
    "pilot_high_accuracy",
    "evaluation",
    "dev_debug",
}


def test_all_profile_configs_load_and_validate():
    available = set(get_available_product_profiles())

    assert EXPECTED_PROFILES <= available
    for name in EXPECTED_PROFILES:
        profile = load_product_profile(name)
        assert profile["profile_name"] == name
        assert validate_product_profile(profile) == []


def test_unknown_profile_safely_falls_back_to_default():
    profile = load_product_profile("does_not_exist")

    assert profile["profile_name"] == "default"


def test_unknown_profile_strict_raises():
    with pytest.raises(FileNotFoundError):
        load_product_profile("does_not_exist", strict=True)


def test_path_traversal_profile_name_falls_back_safely():
    profile = load_product_profile("../production_safe")

    assert profile["profile_name"] == "default"


def test_path_traversal_profile_name_strict_raises():
    with pytest.raises(ValueError):
        load_product_profile("../production_safe", strict=True)


def test_loader_does_not_mutate_loaded_profile_objects():
    profile = load_product_profile("production_safe")
    profile["features"]["audit"] = False

    fresh = load_product_profile("production_safe")

    assert fresh["features"]["audit"] is True


def test_load_product_profile_from_path_is_useful_for_tests(tmp_path):
    path = tmp_path / "custom.json"
    path.write_text(
        json.dumps(load_product_profile("default"), ensure_ascii=False),
        encoding="utf-8",
    )

    profile = load_product_profile_from_path(path)

    assert profile["profile_name"] == "default"
    assert validate_product_profile(profile) == []


def test_production_safe_expected_settings():
    profile = load_product_profile("production_safe")

    assert profile["runtime_serving"] is True
    assert profile["features"]["audit"] is True
    assert profile["features"]["feedback"] is True
    assert profile["features"]["review_queue"] is True
    assert profile["features"]["metrics"] is True
    assert profile["features"]["feature_rerank"] is True
    assert profile["features"]["feedback_preview_rerank"] is False
    assert profile["features"]["debug_comparison"] is False
    assert profile["features"]["llm_answer"] is False
    assert profile["features"]["llm_rerank"] is False
    assert profile["answer_policy"]["allow_exact_answer"] is True
    assert profile["answer_policy"]["allow_similar_auto_answer"] is False
    assert profile["answer_policy"]["force_candidate_only_for_similar"] is True


def test_production_low_cost_expected_settings():
    profile = load_product_profile("production_low_cost")

    assert profile["runtime_serving"] is True
    assert profile["cost_policy"]["budget_tier"] == "low"
    assert profile["cost_policy"]["allow_llm_calls"] is False
    assert profile["features"]["debug_comparison"] is False
    assert profile["features"]["feedback_preview_rerank"] is False
    assert profile["features"]["combined_rerank"] is False
    assert profile["limits"]["max_candidates_internal"] <= 3
    assert profile["limits"]["max_candidates_display"] <= 2


def test_pilot_high_accuracy_expected_settings():
    profile = load_product_profile("pilot_high_accuracy")

    assert profile["runtime_serving"] is True
    assert profile["features"]["feedback"] is True
    assert profile["features"]["review_queue"] is True
    assert profile["features"]["metrics"] is True
    assert profile["features"]["feature_rerank"] is True
    assert profile["features"]["feedback_preview_rerank"] is True
    assert profile["features"]["combined_rerank"] is True
    assert profile["features"]["llm_answer"] is False
    assert profile["answer_policy"]["allow_similar_auto_answer"] is False


def test_evaluation_expected_settings():
    profile = load_product_profile("evaluation")

    assert profile["runtime_serving"] is False
    assert profile["features"]["debug_comparison"] is True
    assert profile["features"]["feature_rerank"] is True
    assert profile["features"]["feedback_preview_rerank"] is True
    assert profile["cost_policy"]["allow_batch_eval_only"] is True
    assert profile["answer_policy"]["allow_similar_auto_answer"] is False


def test_dev_debug_expected_settings():
    profile = load_product_profile("dev_debug")

    assert profile["runtime_serving"] is False
    assert profile["features"]["debug_comparison"] is True
    assert profile["features"]["feature_rerank"] is True
    assert profile["features"]["feedback_preview_rerank"] is True
    assert profile["answer_policy"]["allow_similar_auto_answer"] is False


def test_validate_product_profile_reports_schema_errors():
    bad = load_product_profile("default")
    bad["answer_policy"]["allow_similar_auto_answer"] = True
    del bad["limits"]["max_candidates_display"]

    errors = validate_product_profile(bad)

    assert "answer_policy.allow_similar_auto_answer must remain false" in errors
    assert "limits.max_candidates_display is required" in errors
