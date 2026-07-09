from __future__ import annotations

# Safe staging-to-served collection promotion workflow (Prompt056).
#
# Operator-invoked, default-off planning/approval workflow. It validates a
# reviewed NON-production staging set (import-manifest clean), refuses the
# production/default collection outright, records the required tenant-isolation
# check + backup point, and produces an approval report with a rollback plan.
#
# It does NOT itself mutate any vectorstore: it is a gate + plan. Actual ingest
# into the explicit non-production served collection and restore are performed by
# the existing tested tools (ingest path / scripts/backup.sh / scripts/restore.sh)
# only after approval. No secrets, no raw document text, no tenant-private data
# in the report.

from typing import Any, Dict, List, Optional

from scripts.import_manifest import build_manifest
from webapi.ingestion_jobs import is_production_collection

ISOLATION_CHECK_CMD = "scripts/persistence_isolation_check.sh"
BACKUP_CMD = "scripts/backup.sh --output-dir backups"
RESTORE_CMD = "scripts/restore.sh <backup_archive> --target <staging_target>"


def plan_promotion(
    inputs: List[str],
    served_collection: str,
    *,
    expected_tenant: Optional[str] = None,
    prior_backup: Optional[str] = None,
) -> Dict[str, Any]:
    # Returns an approval plan. `ok` is True only when the served target is an
    # explicit non-production collection AND the import manifest is clean.
    served = str(served_collection or "").strip()
    reasons: List[str] = []

    non_production = bool(served) and not is_production_collection(served)
    if not served:
        reasons.append("served_collection_required")
    elif not non_production:
        reasons.append("refused_production_or_default_collection")

    manifest_clean = False
    issue_counts: Dict[str, int] = {}
    if not inputs:
        reasons.append("inputs_required")
    else:
        try:
            manifest = build_manifest(inputs, expected_tenant=expected_tenant)
            issue_counts = {k: len(v) for k, v in (manifest.get("issues") or {}).items()}
            manifest_clean = bool(manifest.get("ok"))
            if not manifest_clean:
                reasons.append("manifest_not_clean")
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"manifest_error:{type(exc).__name__}")

    ok = non_production and manifest_clean
    return {
        "served_collection": served,
        "expected_tenant": str(expected_tenant or "") or None,
        "checks": {
            "non_production_target": non_production,
            "manifest_clean": manifest_clean,
        },
        "issue_counts": issue_counts,
        # Required operator steps before/around promotion (commands only — never
        # secrets). The isolation check + backup are mandatory gates.
        "required_steps": {
            "tenant_isolation_check": ISOLATION_CHECK_CMD,
            "backup_point": BACKUP_CMD,
        },
        "backup_point": prior_backup,
        "rollback_plan": {
            "description": "restore the prior served collection from the backup point",
            "command": RESTORE_CMD,
            "prior_backup": prior_backup,
        },
        "approved": ok,
        "reasons": reasons,
    }


def approval_report_markdown(plan: Dict[str, Any]) -> str:
    # Human-readable approval report (safe fields only).
    lines = [
        "# Collection Promotion Approval Report",
        "",
        f"- Served collection: `{plan.get('served_collection')}`",
        f"- Expected tenant: `{plan.get('expected_tenant')}`",
        f"- Approved: **{plan.get('approved')}**",
        "",
        "## Gate checks",
        f"- Non-production target: `{plan['checks'].get('non_production_target')}`",
        f"- Manifest clean: `{plan['checks'].get('manifest_clean')}`",
        f"- Issue counts: `{plan.get('issue_counts')}`",
        "",
        "## Required operator steps",
        f"- Tenant isolation check: `{plan['required_steps']['tenant_isolation_check']}`",
        f"- Backup point: `{plan['required_steps']['backup_point']}`",
        "",
        "## Rollback plan",
        f"- {plan['rollback_plan']['description']}",
        f"- Command: `{plan['rollback_plan']['command']}`",
    ]
    if plan.get("reasons"):
        lines += ["", "## Reasons", *[f"- {r}" for r in plan["reasons"]]]
    return "\n".join(lines) + "\n"
