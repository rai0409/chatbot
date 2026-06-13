from __future__ import annotations

# Admin-facing document-ingestion dry-run + job status (Prompt044).
#
# Wraps the existing import-manifest dry-run path (scripts/import_manifest.py)
# to validate a proposed import (duplicate ids / tenant mismatch / collisions)
# WITHOUT mutating any vectorstore. Production/default collections are refused.
# In-memory job registry only; stores issue COUNTS and safe metadata — never raw
# document text, API keys, or secrets.

import threading
import time
from typing import Any, Dict, List, Optional

import config
from scripts.import_manifest import build_manifest


_LOCK = threading.Lock()
_JOBS: Dict[str, Dict[str, Any]] = {}
_SEQ = {"n": 0}


def _production_collection_names() -> set:
    names = {
        str(config.getenv_first("CHROMA_COLLECTION") or "").strip(),
        str(getattr(config, "VECTORSTORE_COLLECTION_NAME", "") or "").strip(),
    }
    return {n for n in names if n}


def is_production_collection(collection: Optional[str]) -> bool:
    name = str(collection or "").strip()
    return name in _production_collection_names() or name in {"", "default"}


def reset() -> None:
    with _LOCK:
        _JOBS.clear()
        _SEQ["n"] = 0


def _new_job_id() -> str:
    with _LOCK:
        _SEQ["n"] += 1
        return f"job-{_SEQ['n']}"


def run_dry_run(
    inputs: List[str],
    *,
    expected_tenant: Optional[str] = None,
    collection: Optional[str] = None,
    clock=time.time,
) -> Dict[str, Any]:
    # Dry-run only: builds the import manifest and records a job. Never ingests,
    # never touches a vectorstore. A collection may be supplied for labeling only
    # and MUST be an explicit non-production collection.
    if collection is not None and is_production_collection(collection):
        raise ValueError("refusing production/default collection for ingestion")

    job_id = _new_job_id()
    record: Dict[str, Any] = {
        "job_id": job_id,
        "status": "running",
        "created_at": clock(),
        "expected_tenant": str(expected_tenant or "") or None,
        "collection": str(collection or "") or None,
        "mode": "dry_run",
    }
    with _LOCK:
        _JOBS[job_id] = record

    try:
        manifest = build_manifest(inputs, expected_tenant=expected_tenant)
        issue_counts = {k: len(v) for k, v in (manifest.get("issues") or {}).items()}
        record.update({
            "status": "ok" if manifest.get("ok") else "issues_found",
            "ok": bool(manifest.get("ok")),
            "issue_counts": issue_counts,
            "files": manifest.get("files", []),
            "finished_at": clock(),
        })
    except Exception as exc:  # noqa: BLE001
        record.update({
            "status": "error",
            "ok": False,
            "error_type": type(exc).__name__,
            "finished_at": clock(),
        })
    with _LOCK:
        _JOBS[job_id] = record
    return dict(record)


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        rec = _JOBS.get(job_id)
        return dict(rec) if rec else None


def list_jobs() -> List[Dict[str, Any]]:
    with _LOCK:
        return sorted((dict(r) for r in _JOBS.values()), key=lambda r: r["created_at"], reverse=True)
