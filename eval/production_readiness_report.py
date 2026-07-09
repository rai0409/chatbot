from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag_core.product_profile import (
    get_available_product_profiles,
    load_product_profile,
    validate_product_profile,
)
from rag_core.product_route_policy import build_route_policy
from rag_core.tenant_profile import load_tenant_profile_mapping, validate_tenant_profile_mapping


DEFAULT_OUTPUT_DIR = ROOT / "artifacts" / "readiness"

KEY_FILES = {
    "admin_auth": ROOT / "webapi" / "admin_auth.py",
    "api_auth": ROOT / "webapi" / "api_auth.py",
    "rate_limit": ROOT / "webapi" / "rate_limit.py",
    "security_operations_doc": ROOT / "docs" / "security_operations.md",
    "webapi_main": ROOT / "webapi" / "main.py",
    "smoke_script": ROOT / "scripts" / "product_readiness_smoke.sh",
    "checklist": ROOT / "docs" / "production_readiness_checklist.md",
    "product_profile_helper": ROOT / "rag_core" / "product_profile.py",
    "product_route_policy": ROOT / "rag_core" / "product_route_policy.py",
    "product_contract": ROOT / "rag_core" / "product_contract.py",
    "product_profiles_dir": ROOT / "configs" / "product_profiles",
    "tenant_mapping": ROOT / "configs" / "product_tenants" / "default.json",
    "tenant_profile_helper": ROOT / "rag_core" / "tenant_profile.py",
    "knowledge_manifest_helper": ROOT / "rag_core" / "knowledge_manifest.py",
    "knowledge_manifest_builder": ROOT / "eval" / "knowledge_manifest_builder.py",
    "knowledge_manifest_docs": ROOT / "docs" / "knowledge_manifest.md",
    "source_metadata_helper": ROOT / "rag_core" / "source_metadata.py",
    "promotion_gate": ROOT / "eval" / "feature_rerank_promotion_gate.py",
    "promotion_decision": ROOT / "artifacts" / "eval" / "feature_rerank_promotion_decision.json",
    "generated_manifest": ROOT / "data" / "knowledge" / "manifest.json",
}

KNOWN_BLOCKERS = [
    "DB persistence / tenant isolation not fully production-grade",
    "/chat tenant runtime wiring not enabled",
    "production rerank not enabled",
    "rollback workflow still manual/config-based",
    "generated manifest may need deployment review",
    "end-to-end deployed server smoke is manual",
]

RECOMMENDED_NEXT_STEPS = [
    "run scripts/product_readiness_smoke.sh",
    "review docs/production_readiness_checklist.md",
    "generate/review knowledge manifest",
    "review tenant mapping",
    "enable ADMIN_AUTH_ENABLED=true with ADMIN_AUTH_TOKEN in production",
    "enable API_AUTH_ENABLED=true and RATE_LIMIT_ENABLED=true before external exposure (see docs/security_operations.md)",
    "keep similar auto-answer disabled",
    "run limited pilot via /chat/product-preview before /chat runtime wiring",
]


def build_report(root: Path = ROOT) -> dict[str, Any]:
    paths = {key: root / path.relative_to(ROOT) for key, path in KEY_FILES.items()}
    repo_summary = _repo_summary(root)
    product_profiles = _product_profiles_summary(root)
    tenant_profiles = _tenant_profiles_summary(root)
    admin_auth = _admin_auth_summary(root, paths)
    security_operations = _security_operations_summary(paths)
    knowledge_manifest = _knowledge_manifest_summary(paths)
    citation_metadata = _citation_metadata_summary(paths)
    rerank_promotion = _rerank_promotion_summary(paths)
    safety_checks = _safety_checks(
        root,
        paths,
        product_profiles=product_profiles,
        tenant_profiles=tenant_profiles,
        admin_auth=admin_auth,
        security_operations=security_operations,
        knowledge_manifest=knowledge_manifest,
        citation_metadata=citation_metadata,
        rerank_promotion=rerank_promotion,
    )
    readiness_decision = _readiness_decision(
        safety_checks=safety_checks,
        product_profiles=product_profiles,
        repo_summary=repo_summary,
        knowledge_manifest=knowledge_manifest,
        rerank_promotion=rerank_promotion,
        tenant_profiles=tenant_profiles,
        admin_auth=admin_auth,
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo_summary": repo_summary,
        "readiness_decision": readiness_decision,
        "safety_checks": safety_checks,
        "product_profiles": product_profiles,
        "tenant_profiles": tenant_profiles,
        "admin_auth": admin_auth,
        "security_operations": security_operations,
        "knowledge_manifest": knowledge_manifest,
        "citation_metadata": citation_metadata,
        "rerank_promotion": rerank_promotion,
        "known_blockers": KNOWN_BLOCKERS,
        "recommended_next_steps": RECOMMENDED_NEXT_STEPS,
    }


def write_report(report: dict[str, Any], output_dir: Path | str = DEFAULT_OUTPUT_DIR) -> tuple[Path, Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "production_readiness_report.json"
    md_path = out / "production_readiness_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


def render_markdown(report: dict[str, Any]) -> str:
    decision = report.get("readiness_decision", {})
    safety = report.get("safety_checks", {})
    product_profiles = report.get("product_profiles", {})
    tenant_profiles = report.get("tenant_profiles", {})
    admin = report.get("admin_auth", {})
    security_ops = report.get("security_operations", {})
    knowledge = report.get("knowledge_manifest", {})
    citation = report.get("citation_metadata", {})
    rerank = report.get("rerank_promotion", {})

    lines = [
        "# Production Readiness Report",
        "",
        f"Generated at: `{report.get('generated_at')}`",
        "",
        f"## Decision: `{decision.get('decision')}`",
        "",
        "### Reasons",
    ]
    lines.extend(_bullets(decision.get("reasons") or ["No reasons recorded."]))
    lines.extend(["", "### Blockers"])
    lines.extend(_bullets(decision.get("blockers") or ["No critical blockers detected by static checks."]))
    lines.extend(["", "### Warnings"])
    lines.extend(_bullets(decision.get("warnings") or ["No warnings recorded."]))

    lines.extend(["", "## Safety Checks", "", "| Check | Status |", "| --- | --- |"])
    for key in sorted(safety):
        lines.append(f"| `{key}` | `{bool(safety[key])}` |")

    lines.extend(["", "## Product Profiles"])
    lines.append(f"Available profiles: `{', '.join(product_profiles.get('available_profiles') or [])}`")
    for name in ("production_safe", "production_low_cost", "pilot_high_accuracy", "evaluation", "dev_debug"):
        summary = product_profiles.get(name) or {}
        lines.append(
            f"- `{name}`: present=`{summary.get('present')}`, runtime_serving=`{summary.get('runtime_serving')}`, "
            f"similar_auto_answer=`{summary.get('allow_similar_auto_answer')}`, enabled_steps=`{summary.get('enabled_steps')}`"
        )

    lines.extend(["", "## Tenant Mapping"])
    lines.append(
        f"Mapping present: `{tenant_profiles.get('mapping_present')}`, default profile: "
        f"`{tenant_profiles.get('default_profile')}`, unknown policy: `{tenant_profiles.get('unknown_tenant_policy')}`"
    )
    lines.append(
        f"Tenants: active=`{tenant_profiles.get('active_count')}`, disabled=`{tenant_profiles.get('disabled_count')}`, "
        f"pilot=`{tenant_profiles.get('pilot_count')}`, total=`{tenant_profiles.get('tenant_count')}`"
    )
    lines.extend(_bullets(tenant_profiles.get("warnings") or [], heading="Tenant warnings"))

    lines.extend(["", "## Admin Auth"])
    lines.append(f"Helper present: `{admin.get('helper_present')}`")
    lines.append(f"Protected routes detected: `{admin.get('protected_routes_detected')}`")
    lines.append(f"Env vars: `{admin.get('env_vars')}`")
    lines.append(f"Default mode: `{admin.get('default_mode')}`")
    lines.append(f"Production guidance: {admin.get('production_guidance')}")

    lines.extend(["", "## Security Operations"])
    lines.append(f"API auth helper present: `{security_ops.get('api_auth_helper_present')}`")
    lines.append(f"Protected POST routes detected: `{security_ops.get('protected_post_routes_detected')}`")
    lines.append(f"API-key tenant authorization present: `{security_ops.get('tenant_authorization_present')}`")
    lines.append(
        f"Rate limiter present: `{security_ops.get('rate_limit_helper_present')}` "
        f"(default off: `{security_ops.get('rate_limit_default_off')}`)"
    )
    lines.append(f"Security operations runbook present: `{security_ops.get('security_operations_doc_present')}`")
    lines.append(f"Env vars: `{security_ops.get('env_vars')}`")
    lines.append(f"Production guidance: {security_ops.get('production_guidance')}")

    lines.extend(["", "## Knowledge And Citation Metadata"])
    lines.append(
        f"Knowledge helper=`{knowledge.get('helper_present')}`, builder=`{knowledge.get('builder_present')}`, "
        f"docs=`{knowledge.get('docs_present')}`, generated manifest=`{knowledge.get('generated_manifest_present')}`"
    )
    lines.append(
        f"Source metadata helper=`{citation.get('source_metadata_helper_present')}`, "
        f"approved QA extended metadata=`{citation.get('approved_qa_extended_metadata_present')}`, "
        f"product contract normalization=`{citation.get('product_contract_normalization_present')}`"
    )

    lines.extend(["", "## Rerank Promotion"])
    lines.append(
        f"Gate present=`{rerank.get('gate_present')}`, decision artifact present=`{rerank.get('decision_artifact_present')}`, "
        f"runtime enabled=`{rerank.get('runtime_enabled')}`, production enabled=`{rerank.get('production_enabled')}`"
    )
    if rerank.get("decision_summary"):
        lines.append(f"Decision summary: `{rerank.get('decision_summary')}`")

    lines.extend(["", "## Known Blockers"])
    lines.extend(_bullets(report.get("known_blockers") or []))
    lines.extend(["", "## Recommended Next Steps"])
    lines.extend(_bullets(report.get("recommended_next_steps") or []))
    return "\n".join(lines) + "\n"


def _repo_summary(root: Path) -> dict[str, Any]:
    branch = _git(root, ["rev-parse", "--abbrev-ref", "HEAD"])
    latest = _git(root, ["rev-parse", "--short", "HEAD"])
    status = _git(root, ["status", "--short"])
    dirty_lines = status.splitlines() if status else []
    return {
        "branch": branch or None,
        "latest_commit": latest or None,
        "dirty_worktree_summary": {
            "dirty": bool(dirty_lines),
            "count": len(dirty_lines),
            "entries": dirty_lines[:20],
        },
    }


def _git(root: Path, args: Sequence[str]) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
    except Exception:
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def _product_profiles_summary(root: Path) -> dict[str, Any]:
    profile_dir = root / "configs" / "product_profiles"
    available = get_available_product_profiles(profile_dir)
    out: dict[str, Any] = {"available_profiles": available, "warnings": []}
    for name in ("production_safe", "production_low_cost", "pilot_high_accuracy", "evaluation", "dev_debug"):
        out[name] = _single_profile_summary(name, profile_dir, available)
        out["warnings"].extend(out[name].get("warnings") or [])
    return out


def _single_profile_summary(name: str, profile_dir: Path, available: Sequence[str]) -> dict[str, Any]:
    if name not in available:
        return {"present": False, "warnings": [f"profile_missing:{name}"]}
    try:
        profile = load_product_profile(name, config_dir=profile_dir, strict=True)
        policy = build_route_policy(profile)
    except Exception as exc:
        return {"present": True, "load_error": type(exc).__name__, "warnings": [f"profile_load_failed:{name}"]}
    features = profile.get("features") if isinstance(profile.get("features"), dict) else {}
    answer = profile.get("answer_policy") if isinstance(profile.get("answer_policy"), dict) else {}
    return {
        "present": True,
        "profile_name": profile.get("profile_name"),
        "operation_mode": profile.get("operation_mode"),
        "runtime_serving": bool(profile.get("runtime_serving")),
        "enabled_steps": list(policy.get("enabled_steps") or []),
        "allow_similar_auto_answer": bool(answer.get("allow_similar_auto_answer")),
        "force_candidate_only_for_similar": bool(answer.get("force_candidate_only_for_similar")),
        "llm_answer": bool(features.get("llm_answer")),
        "llm_rerank": bool(features.get("llm_rerank")),
        "debug_comparison": bool(features.get("debug_comparison")),
        "feedback_preview_rerank": bool(features.get("feedback_preview_rerank")),
        "validation_errors": validate_product_profile(profile),
        "policy_warnings": list(policy.get("warnings") or []),
        "warnings": [],
    }


def _tenant_profiles_summary(root: Path) -> dict[str, Any]:
    mapping_path = root / "configs" / "product_tenants" / "default.json"
    if not mapping_path.exists():
        return {
            "mapping_present": False,
            "default_profile": None,
            "unknown_tenant_policy": None,
            "tenant_count": 0,
            "active_count": 0,
            "disabled_count": 0,
            "pilot_count": 0,
            "warnings": ["tenant_mapping_missing"],
        }
    warnings: list[str] = []
    try:
        mapping = load_tenant_profile_mapping(config_dir=mapping_path.parent)
    except Exception as exc:
        return {
            "mapping_present": True,
            "load_error": type(exc).__name__,
            "default_profile": None,
            "unknown_tenant_policy": None,
            "tenant_count": 0,
            "active_count": 0,
            "disabled_count": 0,
            "pilot_count": 0,
            "warnings": ["tenant_mapping_load_failed"],
        }
    validation_errors = validate_tenant_profile_mapping(mapping)
    warnings.extend(validation_errors)
    tenants = mapping.get("tenants") if isinstance(mapping.get("tenants"), dict) else {}
    statuses = [str(raw.get("status") or "active") for raw in tenants.values() if isinstance(raw, dict)]
    return {
        "mapping_present": True,
        "default_profile": mapping.get("default_profile"),
        "unknown_tenant_policy": mapping.get("unknown_tenant_policy"),
        "tenant_count": len(tenants),
        "active_count": statuses.count("active"),
        "disabled_count": statuses.count("disabled"),
        "pilot_count": statuses.count("pilot"),
        "warnings": warnings,
    }


def _admin_auth_summary(root: Path, paths: dict[str, Path]) -> dict[str, Any]:
    helper_present = paths["admin_auth"].exists()
    main_text = _read_text(paths["webapi_main"])
    protected_routes = main_text.count("Depends(require_admin_auth)")
    return {
        "helper_present": helper_present,
        "protected_routes_detected": protected_routes,
        "env_vars": ["ADMIN_AUTH_ENABLED", "ADMIN_AUTH_TOKEN"],
        "default_mode": "disabled unless ADMIN_AUTH_ENABLED is truthy",
        "admin_auth_enabled_env": _env_truthy(os.getenv("ADMIN_AUTH_ENABLED")),
        "admin_auth_token_configured_env": bool(str(os.getenv("ADMIN_AUTH_TOKEN", "")).strip()),
        "production_guidance": "Set ADMIN_AUTH_ENABLED=true and configure a non-empty ADMIN_AUTH_TOKEN before exposing admin routes.",
    }


def _security_operations_summary(paths: dict[str, Path]) -> dict[str, Any]:
    main_text = _read_text(paths["webapi_main"])
    api_auth_text = _read_text(paths["api_auth"])
    rate_limit_text = _read_text(paths["rate_limit"])
    # Protected POST endpoints share one dependency that enforces API auth and
    # (default-off) rate limiting; counting it covers both guards at once.
    protected_routes = main_text.count("Depends(require_api_auth_rate_limited)")
    tenant_authorization_present = (
        "API_AUTH_TENANT_MAP" in api_auth_text and "_parse_tenant_map" in api_auth_text
    )
    rate_limit_default_off = (
        "RATE_LIMIT_ENABLED" in rate_limit_text and "rate_limit_enabled" in rate_limit_text
    )
    return {
        "api_auth_helper_present": paths["api_auth"].exists(),
        "protected_post_routes_detected": protected_routes,
        "tenant_authorization_present": tenant_authorization_present,
        "rate_limit_helper_present": paths["rate_limit"].exists(),
        "rate_limit_default_off": rate_limit_default_off,
        "security_operations_doc_present": paths["security_operations_doc"].exists(),
        "env_vars": [
            "API_AUTH_ENABLED",
            "API_AUTH_KEYS",
            "API_AUTH_TENANT_MAP",
            "RATE_LIMIT_ENABLED",
            "RATE_LIMIT_REQUESTS_PER_MINUTE",
        ],
        "production_guidance": (
            "Set API_AUTH_ENABLED=true with non-empty API_AUTH_KEYS, configure "
            "API_AUTH_TENANT_MAP for multi-tenant deployments, and enable "
            "RATE_LIMIT_ENABLED=true before external exposure. See "
            "docs/security_operations.md for rotation and secrets handling."
        ),
    }


def _knowledge_manifest_summary(paths: dict[str, Path]) -> dict[str, Any]:
    warnings: list[str] = []
    generated_present = paths["generated_manifest"].exists()
    if not generated_present:
        warnings.append("generated_knowledge_manifest_missing_optional")
    return {
        "helper_present": paths["knowledge_manifest_helper"].exists(),
        "builder_present": paths["knowledge_manifest_builder"].exists(),
        "docs_present": paths["knowledge_manifest_docs"].exists(),
        "generated_manifest_present": generated_present,
        "warnings": warnings,
    }


def _citation_metadata_summary(paths: dict[str, Path]) -> dict[str, Any]:
    approved_text = _read_text(ROOT / "rag_core" / "approved_qa.py")
    product_text = _read_text(ROOT / "rag_core" / "product_contract.py")
    return {
        "source_metadata_helper_present": paths["source_metadata_helper"].exists(),
        "approved_qa_extended_metadata_present": "source_id" in approved_text and "source_title" in approved_text,
        "product_contract_normalization_present": "normalize_citation" in product_text and "normalize_source_metadata" in product_text,
    }


def _rerank_promotion_summary(paths: dict[str, Path]) -> dict[str, Any]:
    artifact = paths["promotion_decision"]
    warnings: list[str] = []
    payload: dict[str, Any] | None = None
    if artifact.exists():
        try:
            loaded = json.loads(artifact.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
            else:
                warnings.append("promotion_decision_artifact_not_object")
        except Exception:
            warnings.append("promotion_decision_artifact_unreadable")
    else:
        warnings.append("feature_rerank_promotion_decision_missing_optional")
    runtime_enabled = _find_bool(payload, "runtime_enabled") if payload else False
    production_enabled = _find_bool(payload, "production_enabled") if payload else False
    return {
        "gate_present": paths["promotion_gate"].exists(),
        "decision_artifact_present": artifact.exists(),
        "decision_summary": _promotion_decision_summary(payload),
        "runtime_enabled": bool(runtime_enabled),
        "production_enabled": bool(production_enabled),
        "warnings": warnings,
    }


def _safety_checks(
    root: Path,
    paths: dict[str, Path],
    *,
    product_profiles: dict[str, Any],
    tenant_profiles: dict[str, Any],
    admin_auth: dict[str, Any],
    security_operations: dict[str, Any],
    knowledge_manifest: dict[str, Any],
    citation_metadata: dict[str, Any],
    rerank_promotion: dict[str, Any],
) -> dict[str, bool]:
    main_text = _read_text(paths["webapi_main"])
    product_contract_text = _read_text(paths["product_contract"])
    production_safe = product_profiles.get("production_safe") if isinstance(product_profiles.get("production_safe"), dict) else {}
    all_profile_summaries = [
        value for key, value in product_profiles.items() if key not in {"available_profiles", "warnings"} and isinstance(value, dict)
    ]
    return {
        "admin_auth_guard_present": bool(admin_auth.get("helper_present") and admin_auth.get("protected_routes_detected", 0) >= 1),
        "admin_auth_default_disabled": not _env_truthy(os.getenv("ADMIN_AUTH_ENABLED")),
        "product_profiles_present": paths["product_profiles_dir"].exists() and bool(product_profiles.get("available_profiles")),
        "tenant_mapping_present": bool(tenant_profiles.get("mapping_present")),
        "tenant_preview_wiring_present": "use_tenant_profile" in main_text and "resolve_tenant_product_profile" in main_text,
        "production_rerank_not_enabled_by_default": bool(not production_safe.get("feedback_preview_rerank")),
        "similar_auto_answer_disabled": all(not summary.get("allow_similar_auto_answer") for summary in all_profile_summaries),
        "approved_similar_candidate_only_present": _candidate_only_contract_present(product_contract_text, main_text),
        "readiness_smoke_script_present": paths["smoke_script"].exists(),
        "knowledge_manifest_helper_present": bool(knowledge_manifest.get("helper_present")),
        "citation_source_metadata_helper_present": bool(citation_metadata.get("source_metadata_helper_present")),
        "feature_rerank_promotion_gate_present": bool(rerank_promotion.get("gate_present")),
        "api_auth_guard_present": bool(
            security_operations.get("api_auth_helper_present")
            and security_operations.get("protected_post_routes_detected", 0) >= 1
        ),
        "api_key_tenant_authorization_present": bool(security_operations.get("tenant_authorization_present")),
        "rate_limit_guard_present": bool(
            security_operations.get("rate_limit_helper_present")
            and security_operations.get("rate_limit_default_off")
            and security_operations.get("protected_post_routes_detected", 0) >= 1
        ),
        "security_operations_doc_present": bool(security_operations.get("security_operations_doc_present")),
    }


def _candidate_only_contract_present(product_contract_text: str, runtime_text: str) -> bool:
    contract_has_mode = "ANSWER_MODE_APPROVED_SIMILAR_CANDIDATE_ONLY" in product_contract_text
    contract_has_value = '"approved_similar_candidate_only"' in product_contract_text
    contract_suppresses_answer = (
        'safe_answer_text = "" if answer_mode == ANSWER_MODE_APPROVED_SIMILAR_CANDIDATE_ONLY'
        in product_contract_text
    )
    runtime_references_mode = (
        "ANSWER_MODE_APPROVED_SIMILAR_CANDIDATE_ONLY" in runtime_text
        or "approved_similar_candidate_only" in runtime_text
    )
    return bool(contract_has_mode and contract_has_value and contract_suppresses_answer and runtime_references_mode)


def _readiness_decision(
    *,
    safety_checks: dict[str, bool],
    product_profiles: dict[str, Any],
    repo_summary: dict[str, Any],
    knowledge_manifest: dict[str, Any],
    rerank_promotion: dict[str, Any],
    tenant_profiles: dict[str, Any],
    admin_auth: dict[str, Any],
) -> dict[str, Any]:
    critical = []
    for key in (
        "admin_auth_guard_present",
        "product_profiles_present",
        "tenant_mapping_present",
        "readiness_smoke_script_present",
        "approved_similar_candidate_only_present",
        "knowledge_manifest_helper_present",
        "citation_source_metadata_helper_present",
        "api_auth_guard_present",
        "api_key_tenant_authorization_present",
        "rate_limit_guard_present",
        "security_operations_doc_present",
    ):
        if not safety_checks.get(key):
            critical.append(key)
    if not safety_checks.get("similar_auto_answer_disabled"):
        critical.append("similar_auto_answer_disabled")
    if not safety_checks.get("production_rerank_not_enabled_by_default"):
        critical.append("production_rerank_not_enabled_by_default")
    if not safety_checks.get("feature_rerank_promotion_gate_present"):
        critical.append("feature_rerank_promotion_gate_present")
    production_safe = product_profiles.get("production_safe") if isinstance(product_profiles.get("production_safe"), dict) else {}
    if production_safe.get("llm_answer") or production_safe.get("llm_rerank"):
        critical.append("production_safe_llm_features_disabled")
    if production_safe.get("debug_comparison"):
        critical.append("production_safe_debug_comparison_disabled")

    warnings: list[str] = []
    warnings.extend(knowledge_manifest.get("warnings") or [])
    warnings.extend(rerank_promotion.get("warnings") or [])
    warnings.extend(tenant_profiles.get("warnings") or [])
    if repo_summary.get("dirty_worktree_summary", {}).get("dirty"):
        warnings.append("git_worktree_dirty")
    if not rerank_promotion.get("decision_artifact_present"):
        warnings.append("feature_rerank_promotion_decision_missing")
    if not knowledge_manifest.get("generated_manifest_present"):
        warnings.append("generated_knowledge_manifest_missing")
    if not admin_auth.get("admin_auth_enabled_env") or not admin_auth.get("admin_auth_token_configured_env"):
        warnings.append("admin_auth_env_requires_production_configuration")
    if rerank_promotion.get("runtime_enabled") or rerank_promotion.get("production_enabled"):
        warnings.append("feature_rerank_promotion_artifact_claims_runtime_or_production_enabled")

    if critical:
        decision = "blocked_for_production"
        reasons = ["critical_static_safety_check_failed"]
    elif warnings:
        decision = "needs_review"
        reasons = ["static_safety_checks_passed_with_review_items"]
    else:
        decision = "ready_for_limited_preview"
        reasons = ["critical_static_safety_checks_passed", "limited_preview_only_human_review_still_required"]
    return {
        "decision": decision,
        "reasons": reasons,
        "blockers": critical,
        "warnings": sorted(set(warnings)),
    }


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _env_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _find_bool(value: Any, key: str) -> bool:
    if isinstance(value, dict):
        if key in value and isinstance(value[key], bool):
            return value[key]
        return any(_find_bool(item, key) for item in value.values())
    if isinstance(value, list):
        return any(_find_bool(item, key) for item in value)
    return False


def _promotion_decision_summary(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    summary: dict[str, Any] = {}
    for key in ("decision", "recommended_scope", "generated_at"):
        if key in payload:
            summary[key] = payload.get(key)
    mode_reports = payload.get("mode_reports")
    if isinstance(mode_reports, list):
        summary["mode_decisions"] = [
            {"mode": item.get("mode"), "decision": item.get("decision")}
            for item in mode_reports
            if isinstance(item, dict)
        ]
    return summary or {"available": True}


def _bullets(items: Sequence[Any], heading: str | None = None) -> list[str]:
    lines: list[str] = []
    if heading and items:
        lines.append(f"{heading}:")
    for item in items:
        lines.append(f"- {item}")
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate production readiness JSON and Markdown reports.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for report outputs.")
    args = parser.parse_args(argv)
    report = build_report(ROOT)
    json_path, md_path = write_report(report, Path(args.output_dir))
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
