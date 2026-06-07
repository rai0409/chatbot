from __future__ import annotations

import copy
from typing import Any, Dict, List


_FEATURE_KEYS = {
    "audit",
    "feedback",
    "review_queue",
    "metrics",
    "feature_rerank",
    "feedback_preview_rerank",
    "combined_rerank",
    "debug_comparison",
    "llm_answer",
    "llm_rerank",
}
_ANSWER_POLICY_KEYS = {
    "allow_exact_answer",
    "allow_similar_auto_answer",
    "force_candidate_only_for_similar",
    "fallback_to_no_answer",
    "human_review_on_low_confidence",
}
_LIMIT_KEYS = {
    "max_candidates_internal",
    "max_candidates_display",
    "max_latency_ms_target",
}
_COST_POLICY_KEYS = {
    "budget_tier",
    "allow_llm_calls",
    "allow_batch_eval_only",
}
_NEVER_ENABLE_FEATURES = {"llm_answer", "llm_rerank"}
_NEVER_ENABLE_ANSWER = {"allow_similar_auto_answer"}
_PRODUCTION_PROFILES = {"production_safe", "production_low_cost"}


def _dict(profile: Dict[str, Any], key: str) -> Dict[str, Any]:
    value = profile.get(key)
    return dict(value) if isinstance(value, dict) else {}


def _warnings_for_safety(profile: Dict[str, Any]) -> List[str]:
    warnings: List[str] = []
    features = _dict(profile, "features")
    answer = _dict(profile, "answer_policy")
    cost = _dict(profile, "cost_policy")
    profile_name = str(profile.get("profile_name") or "")

    if answer.get("allow_similar_auto_answer") is True:
        warnings.append("similar_auto_answer_forced_disabled")
    if features.get("llm_answer") is True or features.get("llm_rerank") is True:
        warnings.append("llm_features_forced_disabled")
    if cost.get("allow_llm_calls") is not True and (features.get("llm_answer") or features.get("llm_rerank")):
        warnings.append("llm_features_disabled_by_cost_policy")
    if profile_name in _PRODUCTION_PROFILES and features.get("debug_comparison"):
        warnings.append("debug_comparison_forced_disabled_in_production")
    if profile_name in {"production_safe", "production_low_cost"} and features.get("feedback_preview_rerank"):
        warnings.append("feedback_preview_forced_disabled_in_production")
    return warnings


def _apply_hard_safety(profile: Dict[str, Any]) -> None:
    features = _dict(profile, "features")
    answer = _dict(profile, "answer_policy")
    cost = _dict(profile, "cost_policy")
    profile_name = str(profile.get("profile_name") or "")

    answer["allow_similar_auto_answer"] = False
    answer["force_candidate_only_for_similar"] = True
    if cost.get("allow_llm_calls") is not True:
        features["llm_answer"] = False
        features["llm_rerank"] = False
    features["llm_answer"] = False
    features["llm_rerank"] = False
    if profile_name in _PRODUCTION_PROFILES:
        features["debug_comparison"] = False
    if profile_name in {"production_safe", "production_low_cost"}:
        features["feedback_preview_rerank"] = False
        if profile_name == "production_low_cost":
            features["combined_rerank"] = False
    profile["features"] = features
    profile["answer_policy"] = answer
    profile["cost_policy"] = cost


def _apply_feature_overrides(profile: Dict[str, Any], overrides: Dict[str, Any], warnings: List[str]) -> None:
    features = _dict(profile, "features")
    for key, value in overrides.items():
        if key not in _FEATURE_KEYS:
            warnings.append(f"unknown_override:features.{key}")
            continue
        if not isinstance(value, bool):
            warnings.append(f"invalid_override:features.{key}")
            continue
        if value is True:
            warnings.append(f"unsafe_enable_ignored:features.{key}")
            continue
        if features.get(key) is True:
            features[key] = False
    profile["features"] = features


def _apply_answer_overrides(profile: Dict[str, Any], overrides: Dict[str, Any], warnings: List[str]) -> None:
    answer = _dict(profile, "answer_policy")
    for key, value in overrides.items():
        if key not in _ANSWER_POLICY_KEYS:
            warnings.append(f"unknown_override:answer_policy.{key}")
            continue
        if not isinstance(value, bool):
            warnings.append(f"invalid_override:answer_policy.{key}")
            continue
        if key in _NEVER_ENABLE_ANSWER and value is True:
            warnings.append(f"unsafe_enable_ignored:answer_policy.{key}")
            continue
        current = answer.get(key)
        if isinstance(current, bool) and value is False and current is True:
            answer[key] = False
        elif current is False and value is True:
            warnings.append(f"unsafe_enable_ignored:answer_policy.{key}")
    answer["allow_similar_auto_answer"] = False
    answer["force_candidate_only_for_similar"] = True
    profile["answer_policy"] = answer


def _apply_limit_overrides(profile: Dict[str, Any], overrides: Dict[str, Any], warnings: List[str]) -> None:
    limits = _dict(profile, "limits")
    for key, value in overrides.items():
        if key not in _LIMIT_KEYS:
            warnings.append(f"unknown_override:limits.{key}")
            continue
        if not isinstance(value, int) or value < 0:
            warnings.append(f"invalid_override:limits.{key}")
            continue
        current = limits.get(key)
        if isinstance(current, int) and value <= current:
            limits[key] = value
        else:
            warnings.append(f"unsafe_increase_ignored:limits.{key}")
    profile["limits"] = limits


def _apply_cost_overrides(profile: Dict[str, Any], overrides: Dict[str, Any], warnings: List[str]) -> None:
    cost = _dict(profile, "cost_policy")
    for key, value in overrides.items():
        if key not in _COST_POLICY_KEYS:
            warnings.append(f"unknown_override:cost_policy.{key}")
            continue
        if key == "budget_tier":
            warnings.append("unsafe_change_ignored:cost_policy.budget_tier")
            continue
        if not isinstance(value, bool):
            warnings.append(f"invalid_override:cost_policy.{key}")
            continue
        if value is False and cost.get(key) is True:
            cost[key] = False
        elif value is True and cost.get(key) is not True:
            warnings.append(f"unsafe_enable_ignored:cost_policy.{key}")
    profile["cost_policy"] = cost


def _apply_overrides(profile: Dict[str, Any], request_overrides: Dict[str, Any] | None, warnings: List[str]) -> None:
    if not request_overrides:
        return
    override_policy = _dict(profile, "override_policy")
    if not override_policy.get("allow_request_overrides", False):
        warnings.append("request_overrides_disabled")
        return

    for key, value in request_overrides.items():
        if key == "features" and isinstance(value, dict):
            _apply_feature_overrides(profile, value, warnings)
        elif key == "answer_policy" and isinstance(value, dict):
            _apply_answer_overrides(profile, value, warnings)
        elif key == "limits" and isinstance(value, dict):
            _apply_limit_overrides(profile, value, warnings)
        elif key == "cost_policy" and isinstance(value, dict):
            _apply_cost_overrides(profile, value, warnings)
        else:
            warnings.append(f"unknown_override:{key}")


def build_route_policy(profile: Dict[str, Any], request_overrides: Dict[str, Any] | None = None) -> Dict[str, Any]:
    working = copy.deepcopy(profile or {})
    warnings = _warnings_for_safety(working)
    _apply_hard_safety(working)
    _apply_overrides(working, request_overrides, warnings)
    _apply_hard_safety(working)

    features = _dict(working, "features")
    enabled_steps = [key for key in sorted(features) if features.get(key) is True]
    return {
        "profile_name": working.get("profile_name") or "default",
        "operation_mode": working.get("operation_mode") or "default_safe",
        "runtime_serving": bool(working.get("runtime_serving", False)),
        "enabled_steps": enabled_steps,
        "answer_policy": _dict(working, "answer_policy"),
        "limits": _dict(working, "limits"),
        "cost_policy": _dict(working, "cost_policy"),
        "warnings": warnings,
    }
