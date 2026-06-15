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
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Dict, List, Optional

import config
from rag_core.document_converters import SUPPORTED_FORMATS, convert_file_to_canonical_chunks
from scripts.ingest_canonical_jsonl import ingest_canonical_rows
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


def _safe_file_label(path: str | Path) -> str:
    return Path(path).name[:160]


def _issue_counts(manifest: Dict[str, Any]) -> Dict[str, int]:
    return {k: len(v) for k, v in (manifest.get("issues") or {}).items()}


def _source_type_counts(chunks: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = Counter(str(c.get("source_type") or c.get("type") or "unknown") for c in chunks)
    return dict(sorted(counts.items()))


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    import json

    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _bounded_warning(*, file_name: str, reason: str, error_type: Optional[str] = None) -> Dict[str, str]:
    item = {
        "file": str(file_name)[:160],
        "reason": str(reason)[:120],
    }
    if error_type:
        item["error_type"] = str(error_type)[:80]
    return item


def _ingest_chunks(chunks: List[Dict[str, Any]], collection: str) -> Dict[str, Any]:
    return ingest_canonical_rows(chunks, collection_name=collection)


def run_raw_document_job(
    inputs: List[str],
    *,
    expected_tenant: str,
    collection: str,
    execute: bool = False,
    clock=time.time,
) -> Dict[str, Any]:
    # Converts supported raw documents to canonical chunks, validates a manifest,
    # and optionally writes those chunks to an explicit non-production staging
    # collection. Responses contain safe counts/filenames only, never chunk text.
    tenant = str(expected_tenant or "").strip()
    target = str(collection or "").strip()
    if not tenant:
        raise ValueError("expected_tenant is required")
    if not target:
        raise ValueError("collection is required")
    if is_production_collection(target):
        raise ValueError("refusing production/default collection for ingestion")

    raw_inputs = [str(p).strip() for p in (inputs or []) if str(p).strip()]
    if not raw_inputs:
        raise ValueError("inputs is required")

    job_id = _new_job_id()
    mode = "raw_document_execute" if execute else "raw_document_dry_run"
    record: Dict[str, Any] = {
        "job_id": job_id,
        "status": "running",
        "created_at": clock(),
        "expected_tenant": tenant,
        "collection": target,
        "mode": mode,
        "vectorstore_mutated": False,
        "index_mutated": False,
        "processed_files": 0,
        "skipped_files": 0,
        "chunks_generated": 0,
        "warnings": [],
        "errors": [],
    }
    with _LOCK:
        _JOBS[job_id] = record

    chunks: List[Dict[str, Any]] = []
    part_paths: List[Path] = []
    processed_files: List[Dict[str, Any]] = []
    warnings: List[Dict[str, str]] = []
    errors: List[Dict[str, str]] = []

    try:
        with TemporaryDirectory(prefix=f"kuraden-ingestion-{job_id}-") as tmp:
            tmp_dir = Path(tmp)
            for raw in raw_inputs:
                path = Path(raw)
                file_name = _safe_file_label(path)
                suffix = path.suffix.lower().lstrip(".")
                if suffix not in SUPPORTED_FORMATS:
                    warnings.append(_bounded_warning(file_name=file_name, reason="unsupported_type"))
                    continue
                if not path.is_file():
                    warnings.append(_bounded_warning(file_name=file_name, reason="missing_file"))
                    continue
                try:
                    converted = convert_file_to_canonical_chunks(path, tenant_id=tenant)
                except Exception as exc:  # noqa: BLE001
                    warnings.append(
                        _bounded_warning(
                            file_name=file_name,
                            reason="conversion_failed",
                            error_type=type(exc).__name__,
                        )
                    )
                    continue
                if not converted:
                    warnings.append(_bounded_warning(file_name=file_name, reason="no_chunks_generated"))
                    continue
                part = tmp_dir / f"part-{len(part_paths) + 1}.jsonl"
                _write_jsonl(part, converted)
                part_paths.append(part)
                chunks.extend(converted)
                processed_files.append({
                    "file": file_name,
                    "source_type": suffix,
                    "chunks": len(converted),
                })

            manifest: Dict[str, Any] = {
                "ok": False,
                "issues": {},
                "files": [],
                "source_docs": {},
            }
            if part_paths:
                manifest = build_manifest(part_paths, expected_tenant=tenant)
            else:
                errors.append(_bounded_warning(file_name="(all)", reason="no_supported_chunks"))

            record.update(
                {
                    "processed_files": len(processed_files),
                    "skipped_files": len(raw_inputs) - len(processed_files),
                    "files": processed_files,
                    "chunks_generated": len(chunks),
                    "source_type_counts": _source_type_counts(chunks),
                    "issue_counts": _issue_counts(manifest),
                    "manifest_ok": bool(manifest.get("ok")),
                    "warnings": warnings[:50],
                    "errors": errors[:50],
                }
            )

            if execute:
                if warnings or errors:
                    record.update({"status": "issues_found", "ok": False})
                elif not manifest.get("ok"):
                    record.update({"status": "issues_found", "ok": False})
                else:
                    ingest_result = _ingest_chunks(chunks, target)
                    record.update(
                        {
                            "status": "ok",
                            "ok": True,
                            "vectorstore_mutated": True,
                            "ingested_chunks": int(ingest_result.get("ingested") or 0),
                            "ingest_skipped_chunks": int(ingest_result.get("skipped") or 0),
                            "embedding_fingerprint": ingest_result.get("embedding_fingerprint"),
                        }
                    )
            else:
                record.update(
                    {
                        "status": "ok" if manifest.get("ok") and not errors else "issues_found",
                        "ok": bool(manifest.get("ok")) and not errors,
                    }
                )
            record["finished_at"] = clock()
    except Exception as exc:  # noqa: BLE001
        record.update(
            {
                "status": "error",
                "ok": False,
                "error_type": type(exc).__name__,
                "vectorstore_mutated": False,
                "finished_at": clock(),
            }
        )

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
