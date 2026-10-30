"""Applying the pending changes of a source to its staging collection, with a mass-change guard (step 5-2)."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

import config
from rag_core import change_ledger, source_registry as registry, source_sync
from rag_core.source_registry import SourceError
from rag_core.source_scan import diff_manifests, document_name, name_conflicts

Ingest = Callable[[Sequence[Path], str, str], Dict[str, Any]]
Delete = Callable[[str, str, Sequence[str]], Dict[str, Any]]


def default_ingest(paths: Sequence[Path], tenant: str, collection: str) -> Dict[str, Any]:
    """Ingest files into the staging collection with the ingestion job (replaces the rows of each document)."""
    from webapi import ingestion_jobs

    job = ingestion_jobs.run_raw_document_job(
        [str(p) for p in paths], expected_tenant=tenant, collection=collection, execute=True
    )
    return {"ok": job.get("status") == "ok", "status": job.get("status"), "job_id": job.get("job_id"),
            "warnings": job.get("warnings") or [], "errors": job.get("errors") or []}


def default_delete(collection: str, tenant: str, documents: Sequence[str]) -> Dict[str, Any]:
    """Delete documents (by name) from the staging collection; a collection that does not exist yet has nothing to delete."""
    from webapi import collection_admin

    try:
        return collection_admin.delete_documents(collection, tenant=tenant, source_docs=list(documents), execute=True)
    except LookupError:
        return {"chunks": 0, "missing_collection": True}


def _batch_size() -> int:
    try:
        value = int(os.getenv("INGEST_MAX_FILES", "") or 100)
    except ValueError:
        return 100
    return value if value > 0 else 100


# --- the plan and the guard -----------------------------------------------------------------------------


def plan_source(source_id: str) -> Dict[str, Any]:
    """The pending changes of the last scan. Files whose document name is shared are left out and listed as conflicts."""
    registry.get(source_id)
    observed = registry.load_manifest(source_id, "observed")
    applied = registry.load_manifest(source_id, "applied")
    if not observed and not applied:
        raise SourceError("there is no scan of this source yet (scan it first)")
    delta = diff_manifests(applied, observed)
    conflicts = name_conflicts(list(observed))
    blocked = {path for paths in conflicts.values() for path in paths}
    added = [p for p in delta["added"] if p not in blocked]
    modified = [p for p in delta["modified"] if p not in blocked]
    body = {"added": {p: observed[p]["sha256"] for p in added}, "modified": {p: observed[p]["sha256"] for p in modified},
            "deleted": delta["deleted"]}
    return {
        "added": added, "modified": modified, "deleted": delta["deleted"],
        "conflicts": {name: paths for name, paths in conflicts.items() if any(p in delta["added"] + delta["modified"] for p in paths)},
        "counts": {"added": len(added), "modified": len(modified), "deleted": len(delta["deleted"])},
        "applied_files": len(applied), "total_files": len(observed),
        "plan_sha256": hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
    }


def guard(plan: Mapping[str, Any]) -> Dict[str, Any]:
    """{"hold": bool, "reasons": [...]}: whether the plan must be approved by a person before it is applied."""
    counts, applied = plan["counts"], int(plan["applied_files"])
    deleted = counts["deleted"]
    reasons: List[str] = []
    if applied > 0 and deleted == applied:
        reasons.append("all_deleted")
    if (deleted >= int(config.SOURCE_MIN_DELETES_FOR_GUARD) and applied > 0
            and deleted / applied > float(config.SOURCE_MAX_DELETE_FRACTION)):
        reasons.append("mass_delete")
    if sum(counts.values()) > int(config.SOURCE_MAX_CHANGES):
        reasons.append("too_many_changes")
    return {"hold": bool(reasons), "reasons": reasons}


def approve(source_id: str, plan_sha256: str, *, actor: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Record that `actor` approved this exact plan. Raises when the plan is no longer the one given."""
    record = registry.get(source_id)
    who = str(actor or "").strip()
    if not who or len(who) > 64:
        raise SourceError("an approver (1 to 64 characters) is required")
    plan = plan_source(source_id)
    if plan["plan_sha256"] != plan_sha256:
        raise SourceError("the plan changed since it was shown; review the new plan")
    entry = change_ledger.record("approve", source_id=source_id, tenant_id=record["tenant_id"],
                                 summary={"plan_sha256": plan_sha256, "approved_by": who, "counts": plan["counts"],
                                          "guard": guard(plan)["reasons"]}, now=now)
    return {"approved": plan_sha256, "by": who, "run_id": entry["run_id"]}


# --- applying -------------------------------------------------------------------------------------------


def _ingest_batch(paths: List[Path], tenant: str, collection: str, ingest: Ingest, failed: List[Dict[str, str]]) -> List[Path]:
    """The paths that were ingested. A failed batch is retried file by file; files that still fail go to `failed`."""
    result = ingest(paths, tenant, collection)
    if result.get("ok"):
        return list(paths)
    if len(paths) == 1:
        reason = next((w.get("reason") for w in (result.get("warnings") or result.get("errors") or []) if w.get("reason")),
                      str(result.get("status") or "failed"))
        failed.append({"path": paths[0].name, "reason": str(reason)})
        return []
    done: List[Path] = []
    for path in paths:
        done.extend(_ingest_batch([path], tenant, collection, ingest, failed))
    return done


def apply_source(
    source_id: str, *, approval: Optional[str] = None, actor: str = "", ingest: Optional[Ingest] = None,
    delete: Optional[Delete] = None, now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Apply the plan of the last scan to the staging collection, unless the guard holds it and `approval` is not its SHA."""
    record = registry.get(source_id)
    when = now or datetime.now(timezone.utc)
    plan = plan_source(source_id)
    counts = plan["counts"]
    if sum(counts.values()) == 0:
        return {"status": "nothing_to_do", "counts": counts, "conflicts": plan["conflicts"], "plan_sha256": plan["plan_sha256"]}
    verdict = guard(plan)
    if verdict["hold"] and approval != plan["plan_sha256"]:
        change_ledger.record("hold", source_id=source_id, tenant_id=record["tenant_id"],
                             summary={"reasons": verdict["reasons"], "counts": counts, "plan_sha256": plan["plan_sha256"]}, now=when)
        return {"status": "held", "reasons": verdict["reasons"], "counts": counts, "plan_sha256": plan["plan_sha256"]}

    ingest = ingest or default_ingest
    delete = delete or default_delete
    root, tenant, collection = registry.root_of(record), record["tenant_id"], record["collection"]
    observed = registry.load_manifest(source_id, "observed")
    applied = dict(registry.load_manifest(source_id, "applied"))
    failed: List[Dict[str, str]] = []
    ingested_paths: List[str] = []
    try:
        todo = plan["added"] + plan["modified"]
        size = _batch_size()
        for start in range(0, len(todo), size):
            files = [root / rel for rel in todo[start:start + size]]
            for path in _ingest_batch(files, tenant, collection, ingest, failed):
                rel = path.relative_to(root).as_posix()
                applied[rel] = observed[rel]
                ingested_paths.append(rel)
        deleted_docs = [document_name(rel) for rel in plan["deleted"]]
        if deleted_docs:
            delete(collection, tenant, deleted_docs)
            for rel in plan["deleted"]:
                applied.pop(rel, None)
    except Exception as exc:  # noqa: BLE001 - recorded, then re-raised
        registry.save_manifest(source_id, "applied", applied)  # what did succeed before the failure stays recorded
        change_ledger.record("apply_failed", source_id=source_id, tenant_id=record["tenant_id"],
                             summary={"error": type(exc).__name__, "ingested": len(ingested_paths)}, now=when)
        raise
    registry.save_manifest(source_id, "applied", applied)
    status = "partial" if failed else "applied"
    summary = {"status": status, "ingested": len(ingested_paths), "deleted": len(plan["deleted"]), "failed": failed,
               "counts": counts, "plan_sha256": plan["plan_sha256"], "approved_by": str(actor or "") if verdict["hold"] else None,
               "guard": verdict["reasons"]}
    entry = change_ledger.record("apply", source_id=source_id, tenant_id=record["tenant_id"], summary=summary, now=when)
    registry.update_state(source_id, last_apply_at=when.isoformat())
    return {**summary, "run_id": entry["run_id"], "conflicts": plan["conflicts"]}


def mark_for_reingest(tenant: str, source_doc: str, *, now: Optional[datetime] = None) -> List[str]:
    """Make the next plan re-add a document: its file is removed from the applied manifest of the tenant's sources.

    source_doc is the document name (the file name). Returns the ids of the sources it was found in."""
    marked: List[str] = []
    for record in registry.list_sources(tenant):
        applied = dict(registry.load_manifest(record["source_id"], "applied"))
        hits = [rel for rel in applied if document_name(rel) == source_doc]
        if not hits:
            continue
        for rel in hits:
            applied.pop(rel)
        registry.save_manifest(record["source_id"], "applied", applied)
        change_ledger.record("reingest", source_id=record["source_id"], tenant_id=tenant,
                             summary={"reason": "extraction_correction", "files": len(hits)}, now=now)
        marked.append(record["source_id"])
    return marked


def run_due(*, apply: bool = False, now: Optional[datetime] = None, ingest: Optional[Ingest] = None,
            delete: Optional[Delete] = None) -> List[Dict[str, Any]]:
    """Scan every due source; with apply=True also apply the plans that are not held (a held one waits for a person)."""
    results: List[Dict[str, Any]] = []
    for source_id in source_sync.due_sources(now):
        row: Dict[str, Any] = {"source_id": source_id}
        scan = source_sync.scan_source(source_id, now=now)
        if "error" in scan:
            results.append({**row, "status": scan["error"]})
            continue
        row.update(status="scanned", counts=scan["counts"])
        if apply:
            try:
                outcome = apply_source(source_id, ingest=ingest, delete=delete, now=now)
                row.update(status=outcome["status"], counts=outcome["counts"], reasons=outcome.get("reasons", []))
            except Exception as exc:  # noqa: BLE001 - one source must not stop the others
                row.update(status="apply_failed", error=type(exc).__name__)
        results.append(row)
    return results
