"""Isolated mail-batch ingestion, using the existing staging write boundary.

No new authentication or vectorstore bypass: the router enforces admin/tenant
scope, ingestion_jobs enforces input roots and collection ownership.

Mail state store (per tenant and collection, no plaintext): for each ingested
source_doc the row ids, date, content hash, sender digest, quote references and
a bounded bottom-k sketch of salted 4-gram hashes. It drives update safety
(older / conflicting re-imports of a Message-ID are not written), cross-import
quote matching and the deletion plan. Location:
<dir of INGESTION_REGISTRY_PATH>/mail_state/<tenant>/<collection>.jsonl.
This state is node-local even when INGESTION_REGISTRY_BACKEND=postgres: run mail ingestion on ONE node
(a warning is logged at startup and on first use otherwise). See docs/operations/mail_ingestion.md.

  MAIL_STATE_MAX_MESSAGES         entries kept (default 100000; oldest dropped)
  MAIL_SHINGLE_STORE_MAX_MESSAGES entries that keep a sketch for quote matching
                                  (default 5000; newest kept)
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Dict, Optional

from rag_core import mail_ingest
from rag_core.index_generation import fsync_dir
from rag_core.mail_to_canonical import (apply_update_policy, convert_mail_batch, plan_mail_deletion,
                                        validate_mail_inputs)
from scripts.import_manifest import build_manifest
from webapi import ingestion_jobs as ij

_TENANT_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _int_env(name: str, default: int) -> int:
    try:
        value = int(str(os.getenv(name, "") or "").strip() or default)
    except ValueError:
        return default
    return value if value > 0 else default


_LOCAL_STATE_WARNED = {"done": False}


def warn_local_state_if_shared_registry() -> bool:
    """True (and one warning) when jobs are shared through postgres but mail state stays on this node."""
    if not ij._pg_registry():
        return False
    if not _LOCAL_STATE_WARNED["done"]:
        _LOCAL_STATE_WARNED["done"] = True
        logging.getLogger(__name__).warning(
            "INGESTION_REGISTRY_BACKEND=postgres but the mail state store is local to this node: run mail "
            "ingestion on a single node only, or imports on other nodes will not see this state "
            "(update safety, quote matching and deletion plans would be wrong)")
    return True


def mail_state_path(tenant: str, collection: str) -> Path:
    if not _TENANT_RE.fullmatch(tenant):
        raise ValueError("invalid_mail_tenant_id")
    ij.validate_collection_name(collection)
    return ij.registry_path().parent / "mail_state" / tenant / f"{collection}.jsonl"


def load_mail_state(tenant: str, collection: str) -> Dict[str, Dict[str, Any]]:
    path = mail_state_path(tenant, collection)
    warn_local_state_if_shared_registry()
    if path.is_symlink():
        raise ValueError("mail_state_symlink_forbidden")
    if not path.exists():
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entry = json.loads(line)
            out[str(entry["source_doc"])] = entry
    return out


def _bounded(state: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    def age(entry: Dict[str, Any]) -> tuple:
        dt = mail_ingest.date_utc(str(entry.get("date_iso") or ""))
        return (dt.timestamp() if dt else 0.0, float(entry.get("stored_at") or 0.0))

    ordered = sorted(state.values(), key=age, reverse=True)[:_int_env("MAIL_STATE_MAX_MESSAGES", 100000)]
    keep_sketch = _int_env("MAIL_SHINGLE_STORE_MAX_MESSAGES", 5000)
    for i, entry in enumerate(ordered):
        if i >= keep_sketch:
            entry["sh"], entry["n"] = [], 0
    return {e["source_doc"]: e for e in ordered}


def save_mail_state(tenant: str, collection: str, state: Dict[str, Dict[str, Any]]) -> None:
    path = mail_state_path(tenant, collection)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("mail_state_symlink_forbidden")
    fd, tmp = tempfile.mkstemp(prefix=".mail-state-", suffix=".tmp", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as out:
            for entry in _bounded(state).values():
                out.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
        fsync_dir(path.parent)
    finally:
        Path(tmp).unlink(missing_ok=True)  # already gone after a successful rename


def _prior_for_matching(state: Dict[str, Dict[str, Any]]) -> list:
    return [{"source_doc": e["source_doc"], "participants_h": e.get("participants_h") or [],
             "sh": e.get("sh") or [], "n": int(e.get("n") or 0)}
            for e in state.values() if e.get("sh")]


def delete_mail(tenant: str, collection: str, *, source_doc: str = "", message_id: str = "",
                commit: bool = False) -> Dict[str, Any]:
    """Deletion plan for one mail (chunk ids + source_doc + dependent rows).

    The caller deletes the chunk ids from the vector store / keyword index of
    `collection` (by source_doc) and then calls again with commit=True so the
    state store forgets the message (it is then no quote-match target).
    """
    tenant = str(tenant or "").strip()
    target = str(collection or "").strip()
    if ij.is_production_collection(target):
        raise ValueError("refusing production/default collection for ingestion")
    owner = ij.get_collection(target)
    if owner is not None and owner["owner_tenant"] != tenant:
        raise PermissionError("collection is owned by another tenant")
    # A committing call writes the state store: a backup must not copy it half-written (maintenance lock).
    with (ij.maintenance_shared() if commit else nullcontext()), ij._collection_write_lock(target):
        state = load_mail_state(tenant, target)
        plan = plan_mail_deletion(state, tenant, source_doc=source_doc, message_id=message_id)
        if commit and plan["found"]:
            state.pop(plan["source_doc"], None)
            save_mail_state(tenant, target, state)
        plan["committed"] = bool(commit and plan["found"])
    return plan


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
    mailbox_owner: Optional[str] = None,
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
        state = load_mail_state(tenant, target)
        # Parsing runs in an isolated worker (time/memory limit, no secrets).
        batch = convert_mail_batch(paths, tenant_id=tenant, mailbox_owner=mailbox_owner,
                                   prior=_prior_for_matching(state), isolate=True)
        rows, update_counts, _replaced = apply_update_policy(batch.rows, state)
        entries = {e["source_doc"]: e for e in batch.state}
        counts = dict(batch.summary)
        counts.update(update_counts)
        record.update({
            "processed_files": len(paths), "chunks_generated": len(rows),
            "source_type_counts": {"email": len(rows)}, "mail_counts": counts,
        })
        if rows:
            with TemporaryDirectory(prefix="mail-manifest-") as tmp:
                path = Path(tmp) / "canonical.jsonl"
                ij._write_jsonl(path, rows)
                manifest = build_manifest([path], expected_tenant=tenant)
        else:
            manifest = {"ok": True}
        record.update({"issue_counts": ij._issue_counts(manifest) if rows else {},
                       "manifest_ok": bool(manifest.get("ok"))})
        if not manifest["ok"]:
            record.update({"status": "issues_found", "ok": False})
        elif not execute or not rows:
            record.update({"status": "ok", "ok": True, "nothing_to_write": not rows})
        else:
            # The maintenance lock first (as the document jobs): a backup never sees a half-written collection.
            with ij.maintenance_shared(), ij._collection_write_lock(target):
                # Repeat ownership validation under the lock, then fail closed
                # on backend connection errors BEFORE claiming a collection.
                _validate(inputs, tenant, target)
                ij._claim_collection_for_write(target, tenant, jid, clock=clock)
                write_started = True
                record["write_started"] = True
                ij._save_job(record)
                ingested = ij._ingest_chunks(rows, target)
                ij.register_collection(target, owner_tenant=tenant, status=ij.COLLECTION_READY, job_id=jid)
                current = load_mail_state(tenant, target)
                now = clock()
                for row in rows:
                    entry = dict(entries[row["source_doc"]])
                    entry["stored_at"] = now
                    current[row["source_doc"]] = entry
                save_mail_state(tenant, target, current)
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
            "status": "error", "ok": False,
            "error_type": str(getattr(exc, "error_type", "") or type(exc).__name__)[:80],
            "vectorstore_mutated": write_started,
            "partial_write_possible": write_started,
        })
    record["finished_at"] = clock()
    ij._save_job(record)
    return dict(record)


def submit_mail_job(
    inputs: list[str], *, expected_tenant: str, collection: str,
    execute: bool = False, clock=time.time, mailbox_owner: Optional[str] = None,
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
                         execute=execute, job_id=jid, clock=clock, mailbox_owner=mailbox_owner)
        except Exception as exc:
            failed = dict(record)
            failed.update({"status": "error", "ok": False,
                           "error_type": type(exc).__name__, "finished_at": clock()})
            ij._save_job(failed)

    ij._executor().submit(work)
    return dict(record)
