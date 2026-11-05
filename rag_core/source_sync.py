"""Scanning registered sources and finding the ones that are due (step 5-1)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional

from rag_core import change_ledger, integrations, source_registry as registry
from rag_core.source_registry import SourceError, SourceUnavailable
from rag_core.source_scan import diff_manifests, name_conflicts, scan_folder

# kind -> function(record) that brings the local mirror of a remote source up to date (step 5-3 registers "sharepoint").
PULLERS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {}


def _builtin_puller(kind: str) -> Optional[Callable[[Dict[str, Any]], Dict[str, Any]]]:
    if kind == "sharepoint":
        from rag_core.connectors import sharepoint

        return sharepoint.pull
    return None


def _changes_list(delta: Mapping[str, List[str]]) -> List[Dict[str, str]]:
    return [{"path": path, "op": op} for op in ("added", "modified", "deleted") for path in delta[op]]


def scan_source(source_id: str, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Scan, save the observed manifest, diff it against the applied one and write the ledger entry.

    The changes stay pending: nothing is written to a collection here. An unavailable source is recorded as a failed scan
    and reported as {"error": "source_unavailable"}; it never turns into "everything was deleted"."""
    record = registry.get(source_id)
    when = now or datetime.now(timezone.utc)
    try:
        pulled: Dict[str, Any] = {}
        if record["kind"] != "folder":
            puller = PULLERS.get(record["kind"])
            if puller is None:
                puller = _builtin_puller(record["kind"])
                if puller is not None and record["kind"] in integrations.GATED:
                    integrations.require(record["kind"], record["tenant_id"])  # the three layers; IntegrationDisabled below
            if puller is None:
                raise SourceError(f"no connector for kind '{record['kind']}'")
            pulled = puller(record)
        applied = registry.load_manifest(source_id, "applied")
        previous = registry.load_manifest(source_id, "observed") or applied
        # The applied manifest determines real document ingestion. Never reuse
        # a hash based on filesystem timestamps for this safety-critical path.
        scanned = scan_folder(registry.root_of(record), previous=previous, force_rehash=True)
    except SourceUnavailable:
        entry = change_ledger.record("scan_failed", source_id=source_id, tenant_id=record["tenant_id"],
                                     summary={"reason": "source_unavailable"}, now=when)
        registry.update_state(source_id, last_scan_at=when.isoformat(), last_scan_run=entry["run_id"],
                              last_error="source_unavailable")
        return {"error": "source_unavailable", "run_id": entry["run_id"]}
    except integrations.IntegrationDisabled as exc:
        # Off on purpose, not a failure of the source: recorded as a failed scan, and (as always) never as "all deleted".
        reason = f"integration_off:{exc.status.reason}"
        entry = change_ledger.record("scan_failed", source_id=source_id, tenant_id=record["tenant_id"],
                                     summary={"reason": reason}, now=when)
        registry.update_state(source_id, last_scan_at=when.isoformat(), last_scan_run=entry["run_id"], last_error=reason)
        return {"error": "integration_disabled", "reason": exc.status.reason, "run_id": entry["run_id"]}
    registry.save_manifest(source_id, "observed", scanned.manifest)
    delta = diff_manifests(applied, scanned.manifest)
    conflicts = name_conflicts(list(scanned.manifest))
    summary = {
        "added": len(delta["added"]), "modified": len(delta["modified"]), "deleted": len(delta["deleted"]),
        "total_files": len(scanned.manifest), "applied_files": len(applied), "hashed": scanned.hashed,
        "skipped": {k: (len(v) if isinstance(v, list) else v) for k, v in scanned.skipped.items()},
        "name_conflicts": len(conflicts), "pulled": pulled,
    }
    entry = change_ledger.record("scan", source_id=source_id, tenant_id=record["tenant_id"], summary=summary,
                                 changes=_changes_list(delta), now=when)
    registry.update_state(source_id, last_scan_at=when.isoformat(), last_scan_run=entry["run_id"], last_error=None)
    return {"run_id": entry["run_id"], "counts": {k: summary[k] for k in ("added", "modified", "deleted")},
            "total_files": summary["total_files"], "skipped": scanned.skipped, "name_conflicts": conflicts,
            "changes": delta}


def due_sources(now: Optional[datetime] = None) -> List[str]:
    """Ids of the enabled sources with a schedule whose last scan is at least that many minutes ago (or never)."""
    when = now or datetime.now(timezone.utc)
    due = []
    for record in registry.list_sources():
        minutes = int(record["schedule_minutes"])
        if not record["enabled"] or minutes <= 0:
            continue
        last = record["state"].get("last_scan_at")
        if last is None or (when - datetime.fromisoformat(last)).total_seconds() >= minutes * 60:
            due.append(record["source_id"])
    return due
