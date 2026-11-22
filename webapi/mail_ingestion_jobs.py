"""Isolated mail-batch ingestion, using the existing staging write boundary.

No new authentication or vectorstore bypass: the router enforces admin/tenant
scope, ingestion_jobs enforces input roots and collection ownership.
"""
from __future__ import annotations

import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Optional

from rag_core.mail_to_canonical import convert_mail_inputs, validate_mail_inputs
from scripts.import_manifest import build_manifest
from webapi import ingestion_jobs as ij


def _validate(inputs: list[str], tenant: str, collection: str) -> list[Path]:
    if not tenant or not collection:
        raise ValueError("mail_tenant_and_collection_required")
    if ij.is_production_collection(collection):
        raise ValueError("refusing production/default collection for ingestion")
    ij.validate_collection_name(collection)
    paths = ij._resolve_inputs(inputs)
    validate_mail_inputs(paths, tenant_id=tenant)
    owner = ij.get_collection(collection)
    if owner is not None and owner["owner_tenant"] != tenant:
        raise PermissionError("collection is owned by another tenant")
    return paths


def run_mail_job(
    inputs: list[str], *, expected_tenant: str, collection: str,
    execute: bool = False, job_id: Optional[str] = None, clock=time.time,
) -> dict[str, Any]:
    tenant = str(expected_tenant or "").strip()
    target = str(collection or "").strip()
    paths = _validate(inputs, tenant, target)
    jid = job_id or ij._new_job_id()
    record: dict[str, Any] = {
        "job_id": jid, "status": "running", "created_at": clock(),
        "expected_tenant": tenant, "collection": target,
        "mode": "mail_execute" if execute else "mail_dry_run",
        "vectorstore_mutated": False, "index_mutated": False,
        "write_started": False, "owner": ij._owner_token(),
        "processed_files": 0, "chunks_generated": 0,
        "issue_counts": {}, "manifest_ok": False,
    }
    ij._save_job(record)
    write_started = False
    try:
        rows, counts = convert_mail_inputs(paths, tenant_id=tenant)
        with TemporaryDirectory(prefix="mail-manifest-") as tmp:
            path = Path(tmp) / "canonical.jsonl"
            ij._write_jsonl(path, rows)
            manifest = build_manifest([path], expected_tenant=tenant)
        record.update({
            "processed_files": len(paths), "chunks_generated": len(rows),
            "source_type_counts": {"email": len(rows)},
            "mail_counts": counts, "issue_counts": ij._issue_counts(manifest),
            "manifest_ok": bool(manifest.get("ok")),
        })
        if not manifest["ok"]:
            record.update({"status": "issues_found", "ok": False})
        elif not execute:
            record.update({"status": "ok", "ok": True})
        else:
            with ij._collection_write_lock(target):
                # Repeat ownership validation under the lock, then fail closed
                # on backend connection errors BEFORE claiming a collection.
                _validate(inputs, tenant, target)
                ij._claim_collection_for_write(target, tenant, jid, clock=clock)
                write_started = True
                record["write_started"] = True
                ij._save_job(record)
                ingested = ij._ingest_chunks(rows, target)
                ij.register_collection(target, owner_tenant=tenant, status=ij.COLLECTION_READY, job_id=jid)
            record.update({
                "status": "ok", "ok": True, "vectorstore_mutated": True,
                "index_mutated": True,
                "ingested_chunks": int(ingested.get("ingested") or 0),
                "embedding_fingerprint": ingested.get("embedding_fingerprint"),
            })
    except PermissionError:
        record.update({"status": "rejected", "ok": False, "finished_at": clock()})
        ij._save_job(record)
        raise
    except Exception as exc:  # never include potentially sensitive exception text
        record.update({
            "status": "error", "ok": False, "error_type": type(exc).__name__,
            "vectorstore_mutated": write_started,
            "partial_write_possible": write_started,
        })
    record["finished_at"] = clock()
    ij._save_job(record)
    return dict(record)


def submit_mail_job(
    inputs: list[str], *, expected_tenant: str, collection: str,
    execute: bool = False, clock=time.time,
) -> dict[str, Any]:
    tenant = str(expected_tenant or "").strip()
    target = str(collection or "").strip()
    _validate(inputs, tenant, target)
    jid = ij._new_job_id()
    record = {
        "job_id": jid, "status": "queued", "created_at": clock(),
        "expected_tenant": tenant, "collection": target,
        "mode": "mail_execute" if execute else "mail_dry_run",
        "vectorstore_mutated": False, "index_mutated": False,
        "write_started": False, "async": True, "owner": ij._owner_token(),
    }
    ij._save_job(record)

    def work() -> None:
        try:
            run_mail_job(inputs, expected_tenant=tenant, collection=target,
                         execute=execute, job_id=jid, clock=clock)
        except Exception as exc:
            failed = dict(record)
            failed.update({"status": "error", "ok": False,
                           "error_type": type(exc).__name__, "finished_at": clock()})
            ij._save_job(failed)

    ij._executor().submit(work)
    return dict(record)
