from __future__ import annotations

# Admin-facing document-ingestion dry-run + job status (Prompt044).
#
# Wraps the existing import-manifest dry-run path (scripts/import_manifest.py)
# to validate a proposed import (duplicate ids / tenant mismatch / collisions)
# WITHOUT mutating any vectorstore. Production/default collections are refused.
#
# Job records and the staging-collection registry are persisted in SQLite
# (INGESTION_REGISTRY_PATH, default <RUNS_DIR>/ingestion_registry.sqlite3), so
# collection ownership survives a restart. Each staging collection has exactly
# one owner tenant; other tenants can neither write to nor query it. Records
# hold issue COUNTS and safe metadata — never raw document text, keys, secrets.
#
# Inputs are confined to INGEST_ALLOWED_ROOTS (os.pathsep-separated). With no
# root configured every input is refused.

import hashlib
import json
import os
import re
import socket
import sqlite3
import threading
import time
import uuid
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Dict, Iterator, List, Optional

import config
from rag_core.document_converters import SUPPORTED_FORMATS, isolated
from rag_core.ingest_preprocess import PreprocessOptions, preprocess_rows
from rag_core.retrieval import keyword_index_path_for_collection
from scripts.ingest_canonical_jsonl import ingest_canonical_rows, new_generation_id, write_keyword_rows
from scripts.import_manifest import build_manifest


_LOCK = threading.Lock()
_DEFAULT_MAX_FILES = 100
_DEFAULT_MAX_FILE_BYTES = 50 * 1024 * 1024

COLLECTION_READY = "ready"
COLLECTION_PENDING = "pending"
# A collection being (re)written is not served until the write succeeds, so a
# failed or interrupted re-ingest never serves half-written rows (stage 14).
COLLECTION_WRITING = "writing"
# Stage 15: one name rule for every collection the API writes or queries (the
# query side already used it). Names outside it, such as "a b", used to share a
# keyword corpus file with "a_b" after sanitizing.
COLLECTION_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


def validate_collection_name(name: str) -> str:
    target = str(name or "").strip()
    if not COLLECTION_NAME_RE.fullmatch(target) or ".." in target:
        raise ValueError("invalid collection name (letters, digits, _ . - ; up to 128)")
    return target


# --- persistence -------------------------------------------------------------


def registry_path() -> Path:
    raw = str(os.getenv("INGESTION_REGISTRY_PATH", "")).strip()
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = Path(config.BASE_DIR) / path
        return path
    return Path(config.RUNS_DIR) / "ingestion_registry.sqlite3"


def _pg_registry() -> bool:
    return str(os.getenv("INGESTION_REGISTRY_BACKEND", "local")).strip().lower() == "postgres"


class _PgConn:
    """sqlite-style execute("... ? ...") on a psycopg connection."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql: str, params=()):
        return self._conn.execute(sql.replace("?", "%s"), params)


_PG_DDL = (
    "CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY, created_at DOUBLE PRECISION NOT NULL, record TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS collections (name TEXT PRIMARY KEY, owner_tenant TEXT NOT NULL, status TEXT NOT NULL,"
    " created_at DOUBLE PRECISION NOT NULL, updated_at DOUBLE PRECISION NOT NULL, last_job_id TEXT)",
)
_PG_READY = {"done": False}


def _connect() -> sqlite3.Connection:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS jobs ("
        " job_id TEXT PRIMARY KEY,"
        " created_at REAL NOT NULL,"
        " record TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS collections ("
        " name TEXT PRIMARY KEY,"
        " owner_tenant TEXT NOT NULL,"
        " status TEXT NOT NULL,"
        " created_at REAL NOT NULL,"
        " updated_at REAL NOT NULL,"
        " last_job_id TEXT)"
    )
    return conn


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    # Serializes access within the process, commits on success, always closes.
    if _pg_registry():
        # Shared registry for several API nodes (INGESTION_REGISTRY_BACKEND=postgres).
        from webapi import state_backend

        with state_backend.postgres() as pg:
            if not _PG_READY["done"]:
                for ddl in _PG_DDL:
                    pg.execute(ddl)
                _PG_READY["done"] = True
            yield _PgConn(pg)
        return
    with _LOCK:
        conn = _connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()


def _save_job(record: Dict[str, Any]) -> None:
    with _db() as conn:
        upsert = (
            "INSERT INTO jobs (job_id, created_at, record) VALUES (?, ?, ?)"
            " ON CONFLICT (job_id) DO UPDATE SET created_at = EXCLUDED.created_at, record = EXCLUDED.record"
            if _pg_registry()
            else "INSERT OR REPLACE INTO jobs (job_id, created_at, record) VALUES (?, ?, ?)"
        )
        conn.execute(upsert, (record["job_id"], float(record["created_at"]), json.dumps(record, ensure_ascii=False)))


def reset() -> None:
    # Test helper: clears the registry at the current registry_path().
    with _db() as conn:
        conn.execute("DELETE FROM jobs")
        conn.execute("DELETE FROM collections")


def _new_job_id() -> str:
    return f"job-{uuid.uuid4().hex}"


# --- collection registry -------------------------------------------------------


def _production_collection_names() -> set:
    names = {
        str(config.getenv_first("CHROMA_COLLECTION") or "").strip(),
        str(getattr(config, "VECTORSTORE_COLLECTION_NAME", "") or "").strip(),
    }
    return {n for n in names if n}


def is_production_collection(collection: Optional[str]) -> bool:
    name = str(collection or "").strip()
    return name in _production_collection_names() or name in {"", "default"}


def get_collection(name: str) -> Optional[Dict[str, Any]]:
    with _db() as conn:
        row = conn.execute(
            "SELECT name, owner_tenant, status, created_at, updated_at, last_job_id"
            " FROM collections WHERE name = ?",
            (str(name or "").strip(),),
        ).fetchone()
    if row is None:
        return None
    keys = ("name", "owner_tenant", "status", "created_at", "updated_at", "last_job_id")
    return dict(zip(keys, row))


def register_collection(name: str, *, owner_tenant: str, status: str = COLLECTION_PENDING, job_id: str | None = None, clock=time.time) -> Dict[str, Any]:
    # Claims a collection for one tenant. Raises PermissionError when another
    # tenant already owns it. Also used by operators to register collections
    # created outside the API (CLI imports) before they can be queried.
    target = str(name or "").strip()
    tenant = str(owner_tenant or "").strip()
    if not target or not tenant:
        raise ValueError("collection and owner_tenant are required")
    if is_production_collection(target):
        raise ValueError("refusing production/default collection for ingestion")
    validate_collection_name(target)
    now = clock()
    with _db() as conn:
        row = conn.execute("SELECT owner_tenant, status FROM collections WHERE name = ?", (target,)).fetchone()
        if row is not None and row[0] != tenant:
            raise PermissionError("collection is owned by another tenant")
        if row is None:
            conn.execute(
                "INSERT INTO collections (name, owner_tenant, status, created_at, updated_at, last_job_id)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (target, tenant, status, now, now, job_id),
            )
        else:
            new_status = COLLECTION_READY if COLLECTION_READY in (row[1], status) else status
            conn.execute(
                "UPDATE collections SET status = ?, updated_at = ?, last_job_id = COALESCE(?, last_job_id) WHERE name = ?",
                (new_status, now, job_id, target),
            )
    return get_collection(target) or {}


def _vectorstore_has_collection(name: str) -> bool:
    from rag_core import vector_backend

    if vector_backend.backend_name() == "qdrant":
        try:
            return vector_backend.QdrantCollection(name).exists()
        except Exception:  # noqa: BLE001
            return False
    try:
        import chromadb

        client = chromadb.PersistentClient(path=config.VECTORSTORE_DIR)
        collections = client.list_collections()
    except Exception:  # noqa: BLE001
        return False
    for collection in collections:
        collection_name = collection if isinstance(collection, str) else getattr(collection, "name", "")
        if str(collection_name or "").strip() == name:
            return True
    return False


def _claim_collection_for_write(target: str, tenant: str, job_id: str, clock=time.time) -> None:
    existing = get_collection(target)
    if existing is None and _vectorstore_has_collection(target):
        # Created outside the API: ownership is unknown, so refuse instead of
        # letting the first caller take it. Register it explicitly first.
        raise PermissionError("collection exists but is not registered; register its owner first")
    register_collection(target, owner_tenant=tenant, status=COLLECTION_PENDING, job_id=job_id)
    with _db() as conn:
        # Not "ready" while writing, even if it was before (status is otherwise sticky).
        conn.execute(
            "UPDATE collections SET status = ?, updated_at = ?, last_job_id = ? WHERE name = ?",
            (COLLECTION_WRITING, clock(), job_id, target),
        )


@contextmanager
def _collection_write_lock(target: str) -> Iterator[None]:
    """One writer per collection on this node (vector rows + keyword corpus).

    Several workers or a sync and an async request would otherwise interleave
    their read-modify-write of the keyword corpus. Nodes sharing one index
    directory need a shared file system with working locks (see the README).
    """
    import fcntl

    lock_dir = registry_path().parent / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(target.encode("utf-8")).hexdigest()[:24]
    with open(lock_dir / f"collection-{name}.lock", "a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


# --- input confinement ---------------------------------------------------------


def _int_env(name: str, default: int) -> int:
    try:
        value = int(str(os.getenv(name, "")).strip())
    except ValueError:
        return default
    return value if value > 0 else default


def allowed_ingest_roots() -> List[Path]:
    raw = str(os.getenv("INGEST_ALLOWED_ROOTS", "")).strip()
    roots: List[Path] = []
    for part in raw.split(os.pathsep):
        part = part.strip()
        if not part:
            continue
        root = Path(part)
        if not root.is_absolute():
            root = Path(config.BASE_DIR) / root
        roots.append(root.resolve())
    return roots


def resolve_ingest_input(raw: str) -> Path:
    # Returns the resolved path, or raises ValueError when the input is outside
    # every allowed root or is a symlink. Containment is checked on resolved
    # paths, so a symlinked parent that points outside a root is also refused.
    roots = allowed_ingest_roots()
    if not roots:
        raise ValueError("ingest roots are not configured (INGEST_ALLOWED_ROOTS)")
    path = Path(str(raw).strip())
    if not path.is_absolute():
        path = Path(config.BASE_DIR) / path
    if path.is_symlink():
        raise ValueError("symlinked ingest inputs are refused")
    resolved = path.resolve()
    if not any(resolved == root or resolved.is_relative_to(root) for root in roots):
        raise ValueError("input path is outside the allowed ingest roots")
    return resolved


def _resolve_inputs(inputs: List[str]) -> List[Path]:
    raw_inputs = [str(p).strip() for p in (inputs or []) if str(p).strip()]
    if not raw_inputs:
        raise ValueError("inputs is required")
    max_files = _int_env("INGEST_MAX_FILES", _DEFAULT_MAX_FILES)
    if len(raw_inputs) > max_files:
        raise ValueError(f"too many inputs (max {max_files})")
    return [resolve_ingest_input(raw) for raw in raw_inputs]


# --- jobs ----------------------------------------------------------------------


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
    paths = _resolve_inputs(inputs)

    job_id = _new_job_id()
    record: Dict[str, Any] = {
        "job_id": job_id,
        "status": "running",
        "created_at": clock(),
        "expected_tenant": str(expected_tenant or "") or None,
        "collection": str(collection or "") or None,
        "mode": "dry_run",
    }
    _save_job(record)

    try:
        manifest = build_manifest([str(p) for p in paths], expected_tenant=expected_tenant)
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
    _save_job(record)
    return dict(record)


def _safe_file_label(path: str | Path) -> str:
    return Path(path).name[:160]


def _issue_counts(manifest: Dict[str, Any]) -> Dict[str, int]:
    return {k: len(v) for k, v in (manifest.get("issues") or {}).items()}


def _source_type_counts(chunks: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = Counter(str(c.get("source_type") or c.get("type") or "unknown") for c in chunks)
    return dict(sorted(counts.items()))


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
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
    # One generation id for both indexes: the vector rows and the keyword
    # JSONL of this collection are written by the same job and can be
    # checked against each other (scripts/check_index_consistency.py).
    generation = new_generation_id()
    result = ingest_canonical_rows(chunks, collection_name=collection, generation_id=generation)
    keyword = write_keyword_rows(keyword_index_path_for_collection(collection), chunks, generation_id=generation)
    result["keyword_index_rows"] = keyword["written"]
    return result


# Counts copied into job records. Line keys (repeated_line_keys) are left out:
# they are document text and job records must not echo document content.
_PREPROCESS_REPORT_KEYS = (
    "rows_changed",
    "lines_joined",
    "repeated_lines_removed",
    "invalid_quantities",
    "empty_after_preprocess",
)


def _preprocess_converted(converted: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    # PDF rows were already preprocessed page by page inside the converter.
    if all(str(row.get("source_type") or "") == "pdf" for row in converted):
        return converted, {}
    rows, report = preprocess_rows(converted, PreprocessOptions())
    summary = {key: int(report.get(key) or 0) for key in _PREPROCESS_REPORT_KEYS}
    summary["quantities_by_kind"] = dict(report.get("quantities_by_kind") or {})
    return rows, summary


def _convert_workers() -> int:
    # INGEST_CONVERT_WORKERS (default 1 = one file after another, as before):
    # processes that convert the files of one job in parallel (PDF text
    # extraction is CPU-bound Python, so threads would not help).
    try:
        workers = int(os.getenv("INGEST_CONVERT_WORKERS", "") or 1)
    except ValueError:
        workers = 1
    return max(1, min(workers, os.cpu_count() or 1))


# Uploads are stored as <upload dir>/<tenant>/<YYYYMMDD>/<12 hex>_<file name>.
_UPLOAD_NAME_RE = re.compile(r"[0-9a-f]{12}_(.+)")


def document_name(path: Path) -> str:
    """source_doc of a file: its name, without the random prefix of an upload.

    An updated document uploaded again must replace the old version: the
    vector store and the keyword corpus replace rows by (tenant, source_doc).
    """
    match = _UPLOAD_NAME_RE.fullmatch(path.name)
    if match and re.fullmatch(r"\d{8}", path.parent.name):
        return match.group(1)
    return path.name


def _convert_files(paths: List[Path], tenant: str) -> Dict[Path, Any]:
    """path -> chunk list, or the exception the conversion raised.

    Each file is converted in a worker process with a time limit
    (rag_core/document_converters/isolated.py, INGEST_CONVERT_TIMEOUT_SECONDS).
    """
    workers = min(_convert_workers(), max(1, len(paths)))
    return isolated.convert_files([(path, tenant, document_name(path)) for path in paths], workers=workers)


def run_raw_document_job(
    inputs: List[str],
    *,
    expected_tenant: str,
    collection: str,
    execute: bool = False,
    clock=time.time,
    job_id: Optional[str] = None,
) -> Dict[str, Any]:
    # Converts supported raw documents to canonical chunks, validates a manifest,
    # and optionally writes those chunks to an explicit non-production staging
    # collection owned by expected_tenant. Responses contain safe counts and
    # filenames only, never chunk text. PermissionError means the collection
    # belongs to another tenant (or is unregistered but already exists).
    tenant = str(expected_tenant or "").strip()
    target = str(collection or "").strip()
    if not tenant:
        raise ValueError("expected_tenant is required")
    if not target:
        raise ValueError("collection is required")
    if is_production_collection(target):
        raise ValueError("refusing production/default collection for ingestion")
    validate_collection_name(target)

    paths = _resolve_inputs(inputs)
    owner = get_collection(target)
    if owner is not None and owner["owner_tenant"] != tenant:
        raise PermissionError("collection is owned by another tenant")
    max_bytes = _int_env("INGEST_MAX_FILE_BYTES", _DEFAULT_MAX_FILE_BYTES)

    job_id = job_id or _new_job_id()
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
        "owner": _owner_token(),
    }
    _save_job(record)

    chunks: List[Dict[str, Any]] = []
    part_paths: List[Path] = []
    processed_files: List[Dict[str, Any]] = []
    warnings: List[Dict[str, str]] = []
    errors: List[Dict[str, str]] = []
    preprocess_totals: Dict[str, Any] = {}
    write_started = False

    try:
        with TemporaryDirectory(prefix=f"ingestion-{job_id}-") as tmp:
            tmp_dir = Path(tmp)
            eligible = [
                p for p in paths
                if p.suffix.lower().lstrip(".") in SUPPORTED_FORMATS and p.is_file() and p.stat().st_size <= max_bytes
            ]
            conversions = _convert_files(eligible, tenant)
            names = Counter(document_name(p) for p in eligible)
            for path in paths:
                file_name = _safe_file_label(path)
                if names.get(document_name(path), 0) > 1:
                    # Two inputs with one document name would replace each other's rows.
                    warnings.append(_bounded_warning(file_name=file_name, reason="duplicate_document_name"))
                    continue
                suffix = path.suffix.lower().lstrip(".")
                if suffix not in SUPPORTED_FORMATS:
                    warnings.append(_bounded_warning(file_name=file_name, reason="unsupported_type"))
                    continue
                if not path.is_file():
                    warnings.append(_bounded_warning(file_name=file_name, reason="missing_file"))
                    continue
                if path.stat().st_size > max_bytes:
                    warnings.append(_bounded_warning(file_name=file_name, reason="file_too_large"))
                    continue
                converted = conversions.get(path)
                if isinstance(converted, Exception):
                    warnings.append(
                        _bounded_warning(
                            file_name=file_name,
                            reason="conversion_failed",
                            error_type=getattr(converted, "error_type", type(converted).__name__),
                        )
                    )
                    continue
                if not converted:
                    warnings.append(_bounded_warning(file_name=file_name, reason="no_chunks_generated"))
                    continue
                converted, preprocess_summary = _preprocess_converted(converted)
                for key, value in preprocess_summary.items():
                    if key == "quantities_by_kind":
                        totals = preprocess_totals.setdefault(key, {})
                        for kind, count in value.items():
                            totals[kind] = totals.get(kind, 0) + int(count)
                    else:
                        preprocess_totals[key] = preprocess_totals.get(key, 0) + int(value)
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
                    "skipped_files": len(paths) - len(processed_files),
                    "files": processed_files,
                    "chunks_generated": len(chunks),
                    "source_type_counts": _source_type_counts(chunks),
                    "issue_counts": _issue_counts(manifest),
                    "manifest_ok": bool(manifest.get("ok")),
                    "preprocess": preprocess_totals,
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
                    with _collection_write_lock(target):
                        _claim_collection_for_write(target, tenant, job_id, clock=clock)
                        write_started = True
                        record["write_started"] = True
                        _save_job(record)
                        ingest_result = _ingest_chunks(chunks, target)
                        register_collection(target, owner_tenant=tenant, status=COLLECTION_READY, job_id=job_id)
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
    except PermissionError:
        record.update({"status": "rejected", "ok": False, "finished_at": clock()})
        _save_job(record)
        raise
    except Exception as exc:  # noqa: BLE001
        record.update(
            {
                "status": "error",
                "ok": False,
                "error_type": type(exc).__name__,
                # A failure after the write started may have left part of the
                # rows in the collection; never report "unchanged" then.
                "vectorstore_mutated": write_started,
                "partial_write_possible": write_started,
                "finished_at": clock(),
            }
        )

    _save_job(record)
    return dict(record)


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _db() as conn:
        row = conn.execute("SELECT record FROM jobs WHERE job_id = ?", (str(job_id),)).fetchone()
    return json.loads(row[0]) if row else None


def list_jobs(limit: int = 200) -> List[Dict[str, Any]]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT record FROM jobs ORDER BY created_at DESC LIMIT ?", (max(1, int(limit)),)
        ).fetchall()
    return [json.loads(row[0]) for row in rows]


def staging_collection_status(collection: str, *, tenant_id: str) -> Dict[str, Any]:
    # Prompt073 allowlist, now persisted: only a collection registered as ready
    # for the requesting tenant may be queried. Unknown collections are refused
    # (no fallback to "exists in the vectorstore").
    target = str(collection or "").strip()
    tenant = str(tenant_id or "").strip()
    entry = get_collection(target)
    if entry is None:
        return {"allowed": False, "reason": "staging_collection_unknown"}
    if entry["owner_tenant"] != tenant:
        return {"allowed": False, "reason": "staging_collection_forbidden_for_tenant"}
    if entry["status"] != COLLECTION_READY:
        return {"allowed": False, "reason": "staging_collection_not_executed"}
    return {"allowed": True, "reason": "registered_ready_collection"}


# --- interrupted jobs (stage 14) -------------------------------------------------


def _process_start(pid: int) -> Optional[str]:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def _owner_token() -> Dict[str, Any]:
    # Which process runs the job: a hashed host name (no raw name in records),
    # the pid and its start time (pids are reused).
    pid = os.getpid()
    return {"host": hashlib.sha256(socket.gethostname().encode("utf-8")).hexdigest()[:12],
            "pid": pid, "start": _process_start(pid)}


def _owner_alive(owner: Dict[str, Any]) -> Optional[bool]:
    """True/False for a process on this host; None when it ran on another host."""
    if not isinstance(owner, dict) or owner.get("host") != _owner_token()["host"]:
        return None
    pid = int(owner.get("pid") or 0)
    if pid <= 0:
        return False
    start = _process_start(pid)
    return start is not None and start == owner.get("start")


def recover_interrupted_jobs(clock=time.time) -> int:
    """Mark queued/running jobs of dead processes on this host as interrupted.

    Called at startup: a job whose process was stopped (deploy, crash, OOM)
    would otherwise stay "running" forever. Collections it was writing stay in
    "writing" and are not served until a new job succeeds.
    """
    with _db() as conn:
        rows = conn.execute("SELECT record FROM jobs").fetchall()
    recovered = 0
    for (raw,) in rows:
        record = json.loads(raw)
        if record.get("status") not in ("queued", "running"):
            continue
        if _owner_alive(record.get("owner")) is not False:
            continue
        write_started = bool(record.get("write_started"))
        record.update({
            "status": "interrupted", "ok": False, "finished_at": clock(),
            "vectorstore_mutated": write_started, "partial_write_possible": write_started,
        })
        _save_job(record)
        recovered += 1
    return recovered


def shutdown(wait: bool = False) -> None:
    """Application shutdown: stop taking jobs and stop the conversion workers."""
    pool = _EXECUTOR.get("pool")
    if pool is not None:
        pool.shutdown(wait=wait, cancel_futures=True)
        _EXECUTOR["pool"] = None
    isolated.shutdown()


# --- asynchronous jobs (6) ------------------------------------------------------

_EXECUTOR = {"pool": None}


def _executor():
    from concurrent.futures import ThreadPoolExecutor

    with _LOCK:
        if _EXECUTOR["pool"] is None:
            _EXECUTOR["pool"] = ThreadPoolExecutor(
                max_workers=_int_env("INGEST_WORKERS", 1), thread_name_prefix="ingestion"
            )
        return _EXECUTOR["pool"]


def submit_raw_document_job(
    inputs: List[str],
    *,
    expected_tenant: str,
    collection: str,
    execute: bool = False,
    clock=time.time,
) -> Dict[str, Any]:
    """Validate now, run in a worker thread, return the queued job at once.

    Input confinement, size limits and collection ownership are checked before
    the job is queued (the caller gets 400/403 immediately); conversion and
    embedding run in the background. Poll /admin/ingestion/jobs/{job_id}.
    One worker by default (INGEST_WORKERS) so embedding does not starve the API.
    """
    tenant = str(expected_tenant or "").strip()
    target = str(collection or "").strip()
    if not tenant or not target:
        raise ValueError("expected_tenant and collection are required")
    if is_production_collection(target):
        raise ValueError("refusing production/default collection for ingestion")
    validate_collection_name(target)
    _resolve_inputs(inputs)
    owner = get_collection(target)
    if owner is not None and owner["owner_tenant"] != tenant:
        raise PermissionError("collection is owned by another tenant")

    job_id = _new_job_id()
    record = {
        "job_id": job_id,
        "status": "queued",
        "created_at": clock(),
        "expected_tenant": tenant,
        "collection": target,
        "mode": "raw_document_execute" if execute else "raw_document_dry_run",
        "vectorstore_mutated": False,
        "async": True,
        "owner": _owner_token(),
    }
    _save_job(record)

    def _run() -> None:
        try:
            run_raw_document_job(
                list(inputs), expected_tenant=tenant, collection=target, execute=execute, clock=clock, job_id=job_id
            )
        except Exception as exc:  # noqa: BLE001 - recorded on the job
            failed = dict(record)
            failed.update({"status": "error", "ok": False, "error_type": type(exc).__name__, "finished_at": clock()})
            _save_job(failed)

    _executor().submit(_run)
    return dict(record)
