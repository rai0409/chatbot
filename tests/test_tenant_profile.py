from __future__ import annotations

import copy
import json
import sys

import pytest

from rag_core.tenant_profile import (
    build_tenant_route_policy,
    load_tenant_profile_mapping,
    resolve_tenant_product_profile,
    validate_tenant_profile_mapping,
)


AVAILABLE = [
    "default",
    "production_safe",
    "production_low_cost",
    "pilot_high_accuracy",
    "evaluation",
    "dev_debug",
]


def _mapping(**overrides):
    base = {
        "mapping_version": "1",
        "default_profile": "production_safe",
        "unknown_tenant_policy": "default_profile",
        "tenants": {
            "default": {
                "tenant_id": "default",
                "customer_id": "default",
                "profile": "production_safe",
                "allowed_profiles": [
                    "production_safe",
                    "production_low_cost",
                    "pilot_high_accuracy",
                ],
                "status": "active",
                "notes": "Default tenant mapping",
            },
            "tenant-a": {
                "tenant_id": "tenant-a",
                "customer_id": "customer-a",
                "profile": "production_low_cost",
                "allowed_profiles": ["production_safe", "production_low_cost"],
                "status": "active",
            },
            "tenant-b": {
                "tenant_id": "tenant-b",
                "customer_id": "customer-b",
                "profile": "production_safe",
                "allowed_profiles": ["production_safe"],
                "status": "disabled",
            },
            "tenant-c": {
                "tenant_id": "tenant-c",
                "customer_id": "customer-c",
                "profile": "pilot_high_accuracy",
                "allowed_profiles": ["production_safe", "pilot_high_accuracy"],
                "status": "pilot",
            },
        },
    }
    base.update(overrides)
    return base


def test_default_tenant_mapping_loads_and_validates():
    mapping = load_tenant_profile_mapping()

    assert mapping["default_profile"] == "production_safe"
    assert validate_tenant_profile_mapping(mapping, available_profiles=AVAILABLE) == []


def test_unknown_tenant_falls_back_to_production_safe_safely():
    resolved = resolve_tenant_product_profile(
        "missing",
        mapping=_mapping(),
        available_profiles=AVAILABLE,
    )

    assert resolved["decision"] == "fallback_default"
    assert resolved["resolved_profile"] == "production_safe"
    assert "unknown_tenant" in resolved["warnings"]
    assert resolved["reasons"]


def test_unknown_tenant_never_resolves_to_dev_debug_or_evaluation():
    mapping = _mapping(default_profile="dev_debug")
    resolved = resolve_tenant_product_profile(
        "missing",
        mapping=mapping,
        available_profiles=AVAILABLE,
    )

    assert resolved["decision"] == "invalid_mapping"
    assert resolved["resolved_profile"] == "production_safe"
    assert resolved["resolved_profile"] not in {"dev_debug", "evaluation"}


def test_unknown_tenant_reject_policy_returns_rejection_metadata():
    resolved = resolve_tenant_product_profile(
        "missing",
        mapping=_mapping(unknown_tenant_policy="reject"),
        available_profiles=AVAILABLE,
    )

    assert resolved["decision"] == "rejected"
    assert resolved["resolved_profile"] is None
    assert "unknown_tenant_rejected" in resolved["reasons"]


def test_disabled_tenant_returns_disabled_metadata_without_profile():
    resolved = resolve_tenant_product_profile(
        "tenant-b",
        mapping=_mapping(),
        available_profiles=AVAILABLE,
    )

    assert resolved["decision"] == "disabled"
    assert resolved["resolved_profile"] is None
    assert resolved["tenant_status"] == "disabled"
    assert "tenant_disabled" in resolved["warnings"]


def test_requested_profile_within_allowed_profiles_can_resolve():
    resolved = resolve_tenant_product_profile(
        "tenant-a",
        requested_profile="production_safe",
        mapping=_mapping(),
        available_profiles=AVAILABLE,
    )

    assert resolved["decision"] == "resolved"
    assert resolved["resolved_profile"] == "production_safe"
    assert "requested_profile_allowed" in resolved["reasons"]


def test_requested_profile_outside_allowed_profiles_falls_back_safely():
    resolved = resolve_tenant_product_profile(
        "tenant-a",
        requested_profile="pilot_high_accuracy",
        mapping=_mapping(),
        available_profiles=AVAILABLE,
    )

    assert resolved["decision"] == "fallback_default"
    assert resolved["resolved_profile"] == "production_low_cost"
    assert "tenant_profile_request_ignored" in resolved["warnings"]


def test_requested_profile_outside_allowed_profiles_can_be_rejected_in_strict_mode():
    resolved = resolve_tenant_product_profile(
        "tenant-a",
        requested_profile="pilot_high_accuracy",
        mapping=_mapping(),
        available_profiles=AVAILABLE,
        strict=True,
    )

    assert resolved["decision"] == "rejected"
    assert resolved["resolved_profile"] is None


def test_tenant_profile_not_in_allowed_profiles_is_validation_error():
    mapping = _mapping()
    mapping["tenants"]["tenant-a"]["profile"] = "pilot_high_accuracy"

    errors = validate_tenant_profile_mapping(mapping, available_profiles=AVAILABLE)

    assert "tenants.tenant-a.profile must be included in allowed_profiles" in errors


def test_invalid_profile_name_is_validation_error():
    mapping = _mapping()
    mapping["tenants"]["tenant-a"]["profile"] = "missing_profile"

    errors = validate_tenant_profile_mapping(mapping, available_profiles=AVAILABLE)

    assert "tenants.tenant-a.profile is not an available product profile" in errors


def test_empty_tenant_id_resolves_safely_to_default_tenant():
    resolved = resolve_tenant_product_profile(
        "",
        mapping=_mapping(),
        available_profiles=AVAILABLE,
    )

    assert resolved["tenant_id"] == "default"
    assert resolved["decision"] == "resolved"
    assert resolved["resolved_profile"] == "production_safe"


def test_path_traversal_mapping_name_is_rejected(tmp_path):
    (tmp_path / "default.json").write_text(json.dumps(_mapping()), encoding="utf-8")

    with pytest.raises(ValueError):
        load_tenant_profile_mapping("../default", config_dir=tmp_path)


def test_build_tenant_route_policy_keeps_similar_auto_answer_disabled():
    result = build_tenant_route_policy(
        "tenant-c",
        requested_profile="pilot_high_accuracy",
        mapping=_mapping(),
        available_profiles=AVAILABLE,
    )

    assert result["profile_resolution"]["resolved_profile"] == "pilot_high_accuracy"
    assert result["route_policy"]["answer_policy"]["allow_similar_auto_answer"] is False
    assert result["route_policy"]["answer_policy"]["force_candidate_only_for_similar"] is True


def test_requested_profile_cannot_enable_similar_auto_answer_through_tenant_selection():
    result = build_tenant_route_policy(
        "tenant-c",
        requested_profile="pilot_high_accuracy",
        mapping=_mapping(),
        available_profiles=AVAILABLE,
        request_overrides={"answer_policy": {"allow_similar_auto_answer": True}},
    )

    assert result["route_policy"]["answer_policy"]["allow_similar_auto_answer"] is False
    assert "unsafe_enable_ignored:answer_policy.allow_similar_auto_answer" in result["route_policy"]["warnings"]


def test_mapping_validation_catches_mismatched_tenant_id():
    mapping = _mapping()
    mapping["tenants"]["tenant-a"]["tenant_id"] = "other"

    errors = validate_tenant_profile_mapping(mapping, available_profiles=AVAILABLE)

    assert "tenants.tenant-a.tenant_id must match mapping key" in errors


def test_invalid_mapping_strict_mode_raises():
    mapping = _mapping(default_profile="evaluation")

    with pytest.raises(ValueError):
        resolve_tenant_product_profile("tenant-a", mapping=mapping, available_profiles=AVAILABLE, strict=True)


def test_resolver_does_not_mutate_mapping():
    mapping = _mapping()
    original = copy.deepcopy(mapping)

    resolve_tenant_product_profile("tenant-a", requested_profile="production_safe", mapping=mapping, available_profiles=AVAILABLE)

    assert mapping == original


def test_no_runtime_webapi_import_is_required():
    sys.modules.pop("webapi.main", None)
    __import__("rag_core.tenant_profile")

    assert "webapi.main" not in sys.modules
