from __future__ import annotations

import copy

from rag_core.product_profile import load_product_profile
from rag_core.product_route_policy import build_route_policy


def test_route_policy_returns_expected_enabled_steps():
    profile = load_product_profile("production_safe")

    policy = build_route_policy(profile)

    assert policy["profile_name"] == "production_safe"
    assert policy["runtime_serving"] is True
    assert "audit" in policy["enabled_steps"]
    assert "feedback" in policy["enabled_steps"]
    assert "review_queue" in policy["enabled_steps"]
    assert "metrics" in policy["enabled_steps"]
    assert "feature_rerank" in policy["enabled_steps"]
    assert "feedback_preview_rerank" not in policy["enabled_steps"]
    assert "debug_comparison" not in policy["enabled_steps"]
    assert "llm_answer" not in policy["enabled_steps"]
    assert "llm_rerank" not in policy["enabled_steps"]


def test_production_safe_disables_similar_auto_answer_llm_debug_and_feedback_preview():
    policy = build_route_policy(load_product_profile("production_safe"))

    assert policy["answer_policy"]["allow_similar_auto_answer"] is False
    assert policy["answer_policy"]["force_candidate_only_for_similar"] is True
    assert "llm_answer" not in policy["enabled_steps"]
    assert "llm_rerank" not in policy["enabled_steps"]
    assert "debug_comparison" not in policy["enabled_steps"]
    assert "feedback_preview_rerank" not in policy["enabled_steps"]


def test_production_low_cost_disables_expensive_features_and_uses_small_limits():
    policy = build_route_policy(load_product_profile("production_low_cost"))

    assert policy["cost_policy"]["allow_llm_calls"] is False
    assert "llm_answer" not in policy["enabled_steps"]
    assert "llm_rerank" not in policy["enabled_steps"]
    assert "debug_comparison" not in policy["enabled_steps"]
    assert "feedback_preview_rerank" not in policy["enabled_steps"]
    assert "combined_rerank" not in policy["enabled_steps"]
    assert policy["limits"]["max_candidates_internal"] <= 3
    assert policy["limits"]["max_candidates_display"] <= 2


def test_pilot_high_accuracy_enables_review_feedback_feature_rerank_but_not_auto_answer():
    policy = build_route_policy(load_product_profile("pilot_high_accuracy"))

    assert "feedback" in policy["enabled_steps"]
    assert "review_queue" in policy["enabled_steps"]
    assert "metrics" in policy["enabled_steps"]
    assert "feature_rerank" in policy["enabled_steps"]
    assert "feedback_preview_rerank" in policy["enabled_steps"]
    assert "combined_rerank" in policy["enabled_steps"]
    assert policy["answer_policy"]["allow_similar_auto_answer"] is False


def test_evaluation_enables_comparison_but_is_not_runtime_serving():
    policy = build_route_policy(load_product_profile("evaluation"))

    assert policy["runtime_serving"] is False
    assert "debug_comparison" in policy["enabled_steps"]
    assert "feature_rerank" in policy["enabled_steps"]
    assert policy["cost_policy"]["allow_batch_eval_only"] is True
    assert policy["answer_policy"]["allow_similar_auto_answer"] is False


def test_dev_debug_enables_debug_comparison_but_no_similar_auto_answer():
    policy = build_route_policy(load_product_profile("dev_debug"))

    assert policy["runtime_serving"] is False
    assert "debug_comparison" in policy["enabled_steps"]
    assert "feedback_preview_rerank" in policy["enabled_steps"]
    assert policy["answer_policy"]["allow_similar_auto_answer"] is False


def test_request_overrides_cannot_enable_llm_answer_when_llm_calls_false():
    policy = build_route_policy(
        load_product_profile("production_safe"),
        {"features": {"llm_answer": True}},
    )

    assert "llm_answer" not in policy["enabled_steps"]
    assert "unsafe_enable_ignored:features.llm_answer" in policy["warnings"]


def test_request_overrides_cannot_enable_similar_auto_answer():
    policy = build_route_policy(
        load_product_profile("pilot_high_accuracy"),
        {"answer_policy": {"allow_similar_auto_answer": True}},
    )

    assert policy["answer_policy"]["allow_similar_auto_answer"] is False
    assert "unsafe_enable_ignored:answer_policy.allow_similar_auto_answer" in policy["warnings"]


def test_request_overrides_can_reduce_max_candidates_display():
    policy = build_route_policy(
        load_product_profile("pilot_high_accuracy"),
        {"limits": {"max_candidates_display": 2}},
    )

    assert policy["limits"]["max_candidates_display"] == 2


def test_request_overrides_cannot_increase_limits():
    policy = build_route_policy(
        load_product_profile("production_low_cost"),
        {"limits": {"max_candidates_display": 20}},
    )

    assert policy["limits"]["max_candidates_display"] == 2
    assert "unsafe_increase_ignored:limits.max_candidates_display" in policy["warnings"]


def test_request_overrides_can_disable_optional_enabled_features():
    policy = build_route_policy(
        load_product_profile("pilot_high_accuracy"),
        {"features": {"feedback_preview_rerank": False, "combined_rerank": False}},
    )

    assert "feature_rerank" in policy["enabled_steps"]
    assert "feedback_preview_rerank" not in policy["enabled_steps"]
    assert "combined_rerank" not in policy["enabled_steps"]


def test_unknown_override_keys_produce_warnings():
    policy = build_route_policy(
        load_product_profile("production_safe"),
        {
            "unknown": True,
            "features": {"unknown_feature": False},
            "limits": {"unknown_limit": 1},
        },
    )

    assert "unknown_override:unknown" in policy["warnings"]
    assert "unknown_override:features.unknown_feature" in policy["warnings"]
    assert "unknown_override:limits.unknown_limit" in policy["warnings"]


def test_build_route_policy_does_not_mutate_input_profile():
    profile = load_product_profile("pilot_high_accuracy")
    original = copy.deepcopy(profile)

    build_route_policy(
        profile,
        {"features": {"feedback_preview_rerank": False}, "limits": {"max_candidates_display": 1}},
    )

    assert profile == original


def test_hard_safety_forces_unsafe_profile_values_off():
    profile = load_product_profile("default")
    profile["features"]["llm_answer"] = True
    profile["features"]["llm_rerank"] = True
    profile["answer_policy"]["allow_similar_auto_answer"] = True

    policy = build_route_policy(profile)

    assert "llm_answer" not in policy["enabled_steps"]
    assert "llm_rerank" not in policy["enabled_steps"]
    assert policy["answer_policy"]["allow_similar_auto_answer"] is False
    assert "similar_auto_answer_forced_disabled" in policy["warnings"]
