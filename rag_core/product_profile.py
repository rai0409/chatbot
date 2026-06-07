from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Dict, List

import config


DEFAULT_PROFILE_NAME = "default"
PRODUCT_PROFILE_DIR = Path(config.BASE_DIR) / "configs" / "product_profiles"
_PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_REQUIRED_TOP_LEVEL = {
    "profile_name",
    "operation_mode",
    "runtime_serving",
    "features",
    "answer_policy",
    "limits",
    "cost_policy",
    "override_policy",
}
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
_OVERRIDE_POLICY_KEYS = {
    "allow_request_overrides",
    "safe_narrowing_only",
}


def _profile_dir(config_dir: Path | str | None = None) -> Path:
    return Path(config_dir) if config_dir is not None else PRODUCT_PROFILE_DIR


def _safe_profile_name(profile_name: str | None) -> str | None:
    name = (profile_name or DEFAULT_PROFILE_NAME).strip()
    if not name or not _PROFILE_NAME_RE.fullmatch(name):
        return None
    if Path(name).name != name:
        return None
    return name


def load_product_profile_from_path(path: Path | str) -> Dict[str, Any]:
    profile_path = Path(path)
    with profile_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError(f"product profile must be a JSON object: {profile_path}")
    return copy.deepcopy(payload)


def load_product_profile(
    profile_name: str | None = None,
    config_dir: Path | str | None = None,
    *,
    strict: bool = False,
) -> Dict[str, Any]:
    directory = _profile_dir(config_dir)
    safe_name = _safe_profile_name(profile_name)
    if safe_name is None:
        if strict:
            raise ValueError("invalid product profile name")
        safe_name = DEFAULT_PROFILE_NAME

    profile_path = directory / f"{safe_name}.json"
    if not profile_path.exists():
        if strict:
            raise FileNotFoundError(f"unknown product profile: {safe_name}")
        profile_path = directory / f"{DEFAULT_PROFILE_NAME}.json"

    return load_product_profile_from_path(profile_path)


def get_available_product_profiles(config_dir: Path | str | None = None) -> List[str]:
    directory = _profile_dir(config_dir)
    if not directory.exists():
        return []
    return sorted(
        path.stem
        for path in directory.glob("*.json")
        if _safe_profile_name(path.stem) == path.stem
    )


def _require_bool(profile: Dict[str, Any], section: str, keys: set[str], errors: List[str]) -> None:
    values = profile.get(section)
    if not isinstance(values, dict):
        errors.append(f"{section} must be an object")
        return
    for key in sorted(keys):
        if key not in values:
            errors.append(f"{section}.{key} is required")
        elif not isinstance(values[key], bool):
            errors.append(f"{section}.{key} must be boolean")


def validate_product_profile(profile: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    if not isinstance(profile, dict):
        return ["profile must be an object"]

    for key in sorted(_REQUIRED_TOP_LEVEL):
        if key not in profile:
            errors.append(f"{key} is required")

    if not isinstance(profile.get("profile_name"), str) or not profile.get("profile_name", "").strip():
        errors.append("profile_name must be a non-empty string")
    if not isinstance(profile.get("operation_mode"), str) or not profile.get("operation_mode", "").strip():
        errors.append("operation_mode must be a non-empty string")
    if "runtime_serving" in profile and not isinstance(profile.get("runtime_serving"), bool):
        errors.append("runtime_serving must be boolean")

    _require_bool(profile, "features", _FEATURE_KEYS, errors)
    _require_bool(profile, "answer_policy", _ANSWER_POLICY_KEYS, errors)

    limits = profile.get("limits")
    if not isinstance(limits, dict):
        errors.append("limits must be an object")
    else:
        for key in sorted(_LIMIT_KEYS):
            if key not in limits:
                errors.append(f"limits.{key} is required")
            elif not isinstance(limits[key], int) or limits[key] < 0:
                errors.append(f"limits.{key} must be a non-negative integer")

    cost = profile.get("cost_policy")
    if not isinstance(cost, dict):
        errors.append("cost_policy must be an object")
    else:
        if not isinstance(cost.get("budget_tier"), str) or not cost.get("budget_tier", "").strip():
            errors.append("cost_policy.budget_tier must be a non-empty string")
        for key in sorted(_COST_POLICY_KEYS - {"budget_tier"}):
            if key not in cost:
                errors.append(f"cost_policy.{key} is required")
            elif not isinstance(cost[key], bool):
                errors.append(f"cost_policy.{key} must be boolean")

    override = profile.get("override_policy")
    if not isinstance(override, dict):
        errors.append("override_policy must be an object")
    else:
        for key in sorted(_OVERRIDE_POLICY_KEYS):
            if key not in override:
                errors.append(f"override_policy.{key} is required")
            elif not isinstance(override[key], bool):
                errors.append(f"override_policy.{key} must be boolean")

    if isinstance(profile.get("answer_policy"), dict):
        if profile["answer_policy"].get("allow_similar_auto_answer") is True:
            errors.append("answer_policy.allow_similar_auto_answer must remain false")
        if profile["answer_policy"].get("force_candidate_only_for_similar") is not True:
            errors.append("answer_policy.force_candidate_only_for_similar must remain true")
    return errors
