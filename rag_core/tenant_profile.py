from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Dict, Sequence

import config
from rag_core.product_profile import get_available_product_profiles, load_product_profile
from rag_core.product_route_policy import build_route_policy


DEFAULT_MAPPING_NAME = "default"
DEFAULT_SAFE_PROFILE = "production_safe"
TENANT_PROFILE_DIR = Path(config.BASE_DIR) / "configs" / "product_tenants"

_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_UNKNOWN_TENANT_POLICIES = {"default_profile", "reject"}
_TENANT_STATUSES = {"active", "disabled", "pilot"}
_UNSAFE_DEFAULT_PROFILES = {"dev_debug", "evaluation"}


def load_tenant_profile_mapping(
    mapping_name: str | None = None,
    config_dir: Path | str | None = None,
) -> dict:
    safe_name = _safe_mapping_name(mapping_name)
    if safe_name is None:
        raise ValueError("invalid tenant profile mapping name")

    directory = Path(config_dir) if config_dir is not None else TENANT_PROFILE_DIR
    mapping_path = directory / f"{safe_name}.json"
    with mapping_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError(f"tenant profile mapping must be a JSON object: {mapping_path}")
    return copy.deepcopy(payload)


def validate_tenant_profile_mapping(
    mapping: dict,
    available_profiles: Sequence[str] | None = None,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(mapping, dict):
        return ["mapping must be an object"]

    available = _available_profile_set(available_profiles)
    default_profile = _as_str(mapping.get("default_profile"))
    unknown_policy = _as_str(mapping.get("unknown_tenant_policy")) or "default_profile"

    if not _as_str(mapping.get("mapping_version")):
        errors.append("mapping_version must be a non-empty string")
    if not default_profile:
        errors.append("default_profile must be a non-empty string")
    elif default_profile in _UNSAFE_DEFAULT_PROFILES:
        errors.append("default_profile must not be dev_debug or evaluation")
    elif available and default_profile not in available:
        errors.append(f"unknown default_profile: {default_profile}")

    if unknown_policy not in _UNKNOWN_TENANT_POLICIES:
        errors.append("unknown_tenant_policy must be default_profile or reject")

    tenants = mapping.get("tenants")
    if not isinstance(tenants, dict):
        errors.append("tenants must be an object")
        return errors

    for key, raw in tenants.items():
        if not isinstance(raw, dict):
            errors.append(f"tenants.{key} must be an object")
            continue
        tenant_id = _as_str(raw.get("tenant_id"))
        customer_id = _as_str(raw.get("customer_id"))
        profile = _as_str(raw.get("profile"))
        status = _as_str(raw.get("status")) or "active"
        allowed_profiles = _safe_string_list(raw.get("allowed_profiles"))

        if not tenant_id:
            errors.append(f"tenants.{key}.tenant_id must be a non-empty string")
        elif tenant_id != str(key):
            errors.append(f"tenants.{key}.tenant_id must match mapping key")
        if not customer_id:
            errors.append(f"tenants.{key}.customer_id must be a non-empty string")
        if status not in _TENANT_STATUSES:
            errors.append(f"tenants.{key}.status must be active, disabled, or pilot")
        if not profile:
            errors.append(f"tenants.{key}.profile must be a non-empty string")
        elif available and profile not in available:
            errors.append(f"tenants.{key}.profile is not an available product profile")
        if not allowed_profiles:
            errors.append(f"tenants.{key}.allowed_profiles must include at least one profile")
        else:
            for allowed in allowed_profiles:
                if available and allowed not in available:
                    errors.append(f"tenants.{key}.allowed_profiles contains unknown profile: {allowed}")
            if profile and profile not in allowed_profiles:
                errors.append(f"tenants.{key}.profile must be included in allowed_profiles")
        if status in {"active", "pilot"} and profile in _UNSAFE_DEFAULT_PROFILES:
            errors.append(f"tenants.{key}.profile must not be dev_debug or evaluation for runtime tenants")
    return errors


def resolve_tenant_product_profile(
    tenant_id: str | None,
    *,
    customer_id: str | None = None,
    requested_profile: str | None = None,
    mapping: dict | None = None,
    available_profiles: Sequence[str] | None = None,
    strict: bool = False,
) -> dict:
    active_mapping = copy.deepcopy(mapping) if isinstance(mapping, dict) else load_tenant_profile_mapping()
    available = _available_profile_set(available_profiles)
    errors = validate_tenant_profile_mapping(active_mapping, available_profiles=sorted(available) if available else None)
    tenant_key = _as_str(tenant_id) or "default"
    requested = _as_str(requested_profile) or None
    default_profile = _safe_default_profile(active_mapping.get("default_profile"), available)

    base = {
        "tenant_id": tenant_key,
        "customer_id": _as_str(customer_id) or None,
        "resolved_profile": None,
        "requested_profile": requested,
        "default_profile": default_profile,
        "allowed_profiles": [],
        "tenant_status": None,
        "decision": "resolved",
        "reasons": [],
        "warnings": [],
    }

    if errors:
        if strict:
            raise ValueError("invalid tenant profile mapping: " + "; ".join(errors))
        base.update(
            {
                "resolved_profile": default_profile,
                "allowed_profiles": [default_profile],
                "tenant_status": "unknown",
                "decision": "invalid_mapping",
                "reasons": errors,
                "warnings": ["tenant_profile_mapping_invalid", "safe_default_profile_selected"],
            }
        )
        return base

    tenants = active_mapping.get("tenants") if isinstance(active_mapping.get("tenants"), dict) else {}
    tenant = tenants.get(tenant_key)
    if not isinstance(tenant, dict):
        policy = _as_str(active_mapping.get("unknown_tenant_policy")) or "default_profile"
        base["tenant_status"] = "unknown"
        base["allowed_profiles"] = [default_profile]
        if policy == "reject":
            base["decision"] = "rejected"
            base["reasons"] = ["unknown_tenant_rejected"]
            base["warnings"] = ["unknown_tenant"]
            return base
        base["resolved_profile"] = default_profile
        base["decision"] = "fallback_default"
        base["reasons"] = ["unknown_tenant_default_profile"]
        base["warnings"] = _unknown_tenant_warnings(default_profile)
        return base

    status = _as_str(tenant.get("status")) or "active"
    allowed_profiles = _safe_string_list(tenant.get("allowed_profiles"))
    tenant_profile = _as_str(tenant.get("profile")) or default_profile
    base["customer_id"] = _as_str(customer_id) or _as_str(tenant.get("customer_id")) or None
    base["allowed_profiles"] = allowed_profiles
    base["tenant_status"] = status

    if status == "disabled":
        base["decision"] = "disabled"
        base["reasons"] = ["tenant_disabled"]
        base["warnings"] = ["tenant_disabled"]
        return base

    if requested:
        if requested in allowed_profiles and _profile_is_available_or_unchecked(requested, available):
            base["resolved_profile"] = requested
            base["decision"] = "resolved"
            base["reasons"] = ["requested_profile_allowed"]
            return base
        if strict:
            base["decision"] = "rejected"
            base["reasons"] = ["requested_profile_not_allowed"]
            base["warnings"] = ["tenant_profile_request_rejected"]
            return base
        base["resolved_profile"] = tenant_profile if tenant_profile in allowed_profiles else default_profile
        base["decision"] = "fallback_default"
        base["reasons"] = ["requested_profile_not_allowed"]
        base["warnings"] = ["tenant_profile_request_ignored", "tenant_profile_fallback_selected"]
        return base

    base["resolved_profile"] = tenant_profile
    base["decision"] = "resolved"
    base["reasons"] = ["tenant_profile_selected"]
    return base


def build_tenant_route_policy(
    tenant_id: str | None,
    *,
    customer_id: str | None = None,
    requested_profile: str | None = None,
    mapping: dict | None = None,
    profile_config_dir: Path | str | None = None,
    available_profiles: Sequence[str] | None = None,
    request_overrides: Dict[str, Any] | None = None,
    strict: bool = False,
) -> dict:
    resolution = resolve_tenant_product_profile(
        tenant_id,
        customer_id=customer_id,
        requested_profile=requested_profile,
        mapping=mapping,
        available_profiles=available_profiles,
        strict=strict,
    )
    profile_name = resolution.get("resolved_profile") or resolution.get("default_profile") or DEFAULT_SAFE_PROFILE
    profile = load_product_profile(str(profile_name), config_dir=profile_config_dir)
    route_policy = build_route_policy(profile, request_overrides=request_overrides)
    route_policy["answer_policy"]["allow_similar_auto_answer"] = False
    return {
        "profile_resolution": resolution,
        "route_policy": route_policy,
    }


def _safe_mapping_name(mapping_name: str | None) -> str | None:
    name = (mapping_name or DEFAULT_MAPPING_NAME).strip()
    if not name or not _SAFE_NAME_RE.fullmatch(name):
        return None
    if Path(name).name != name:
        return None
    return name


def _as_str(value: Any) -> str:
    return str(value or "").strip()


def _safe_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in (_as_str(raw) for raw in value) if item]


def _available_profile_set(available_profiles: Sequence[str] | None) -> set[str]:
    if available_profiles is None:
        return set(get_available_product_profiles())
    return {profile for profile in (_as_str(raw) for raw in available_profiles) if profile}


def _safe_default_profile(value: Any, available_profiles: set[str]) -> str:
    candidate = _as_str(value) or DEFAULT_SAFE_PROFILE
    if candidate in _UNSAFE_DEFAULT_PROFILES:
        return DEFAULT_SAFE_PROFILE
    if available_profiles and candidate not in available_profiles:
        return DEFAULT_SAFE_PROFILE if DEFAULT_SAFE_PROFILE in available_profiles else sorted(available_profiles)[0]
    return candidate


def _profile_is_available_or_unchecked(profile: str, available_profiles: set[str]) -> bool:
    return not available_profiles or profile in available_profiles


def _unknown_tenant_warnings(default_profile: str) -> list[str]:
    warnings = ["unknown_tenant", "default_profile_selected"]
    if default_profile in _UNSAFE_DEFAULT_PROFILES:
        warnings.append("unsafe_default_profile_blocked")
    return warnings
