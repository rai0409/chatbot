"""Document ingestion UI, job status, upload, deletion and promotion (moved from main.py, S1).

All under /admin/ingestion. The upload pruning helpers live here; main.py re-exports them."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

import config
from rag_core.retrieval import normalize_tenant_id
from webapi import admin_common
from webapi import ingestion_jobs
from webapi import security_middleware
from webapi.admin_auth import require_admin_auth, require_scope

router = APIRouter()

_STATIC_DIR = admin_common.STATIC_DIR
_request_scope = admin_common.request_scope
_audit_admin = admin_common.audit_admin
_admin_page_html = admin_common.admin_page_html


# --- Document ingestion UI + job status (admin-gated; dry-run, non-prod) ----


class IngestionDryRunRequest(BaseModel):
    inputs: List[str]
    expected_tenant: Optional[str] = None
    collection: Optional[str] = None


class RawDocumentIngestionRequest(BaseModel):
    inputs: List[str]
    expected_tenant: str
    collection: str
    execute: bool = False
    # true: validate, queue, return 202 with the job id (poll the job status).
    async_job: bool = False


@router.get("/admin/ingestion", response_class=HTMLResponse)
def ingestion_page(_admin_auth: None = Depends(require_admin_auth)):
    path = _STATIC_DIR / "ingestion.html"
    try:
        return HTMLResponse(_admin_page_html(path))
    except Exception:
        logging.exception("ingestion page unavailable")
        raise HTTPException(status_code=500, detail="ingestion page unavailable")


@router.post("/admin/ingestion/dry-run")
def ingestion_dry_run(req: IngestionDryRunRequest, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    inputs = [str(p) for p in (req.inputs or []) if str(p).strip()]
    if not inputs:
        raise HTTPException(status_code=400, detail="inputs is required")
    scope = _request_scope(request)
    if scope is not None:
        require_scope(scope, req.expected_tenant)
    # Refuse the production/default collection outright (never mutated anyway;
    # this is a dry-run validation only).
    if req.collection is not None and ingestion_jobs.is_production_collection(req.collection):
        raise HTTPException(status_code=400, detail="refusing production/default collection")
    try:
        return ingestion_jobs.run_dry_run(
            inputs, expected_tenant=req.expected_tenant, collection=req.collection
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/admin/ingestion/raw-documents")
def ingestion_raw_documents(
    req: RawDocumentIngestionRequest,
    request: Request = None,
    _admin_auth: None = Depends(require_admin_auth),
):
    inputs = [str(p) for p in (req.inputs or []) if str(p).strip()]
    if not inputs:
        raise HTTPException(status_code=400, detail="inputs is required")
    if not str(req.expected_tenant or "").strip():
        raise HTTPException(status_code=400, detail="expected_tenant is required")
    scope = _request_scope(request)
    require_scope(scope, req.expected_tenant)
    if req.execute:
        _audit_admin(scope, "ingestion_execute", tenant_id=req.expected_tenant, collection=req.collection,
                     files=len(inputs), async_job=bool(req.async_job))
    if not str(req.collection or "").strip():
        raise HTTPException(status_code=400, detail="collection is required")
    if ingestion_jobs.is_production_collection(req.collection):
        raise HTTPException(status_code=400, detail="refusing production/default collection")
    try:
        if req.async_job:
            queued = ingestion_jobs.submit_raw_document_job(
                inputs,
                expected_tenant=req.expected_tenant,
                collection=req.collection,
                execute=bool(req.execute),
            )
            return JSONResponse(status_code=202, content=queued)
        return ingestion_jobs.run_raw_document_job(
            inputs,
            expected_tenant=req.expected_tenant,
            collection=req.collection,
            execute=bool(req.execute),
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


_UPLOAD_SUFFIXES = {".pdf": (b"%PDF",), ".docx": (b"PK\x03\x04",), ".xlsx": (b"PK\x03\x04",),
                    ".pptx": (b"PK\x03\x04",), ".csv": ()}


def _upload_dir() -> Path:
    # Uploads go under INGEST_UPLOAD_DIR (default: <first ingest root>/uploads),
    # which must itself be inside INGEST_ALLOWED_ROOTS so the ingestion job
    # accepts the saved files.
    roots = ingestion_jobs.allowed_ingest_roots()
    if not roots:
        raise HTTPException(status_code=409, detail="ingest roots are not configured (INGEST_ALLOWED_ROOTS)")
    configured = str(os.getenv("INGEST_UPLOAD_DIR", "")).strip()
    base = Path(configured) if configured else roots[0] / "uploads"
    if not base.is_absolute():
        base = Path(config.BASE_DIR) / base
    resolved = base.resolve()
    if not any(resolved == root or resolved.is_relative_to(root) for root in roots):
        raise HTTPException(status_code=409, detail="INGEST_UPLOAD_DIR must be inside INGEST_ALLOWED_ROOTS")
    return resolved


_UPLOAD_PRUNE = {"last": 0.0}


def _prune_old_uploads(directory: Path) -> int:
    # INGEST_UPLOAD_RETENTION_DAYS (default 30, 0 = keep): uploaded files
    # older than that are removed, at most once an hour. Only files named
    # like uploads ("<12 hex>_<name>") and their emptied day folders.
    try:
        days = float(os.getenv("INGEST_UPLOAD_RETENTION_DAYS", "") or 30)
    except ValueError:
        days = 30.0
    now = time.time()
    if days <= 0 or now - _UPLOAD_PRUNE["last"] < 3600:
        return 0
    _UPLOAD_PRUNE["last"] = now
    removed = 0
    for path in directory.glob("*/*/*"):
        if path.is_file() and re.fullmatch(r"[0-9a-f]{12}_.+", path.name) and now - path.stat().st_mtime > days * 86400:
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
    for day_dir in directory.glob("*/*"):
        if day_dir.is_dir() and not any(day_dir.iterdir()):
            day_dir.rmdir()
    return removed


@router.put("/admin/ingestion/upload")
async def ingestion_upload(
    request: Request,
    filename: str,
    tenant: Optional[str] = None,
    _admin_auth: None = Depends(require_admin_auth),
):
    # Raw request body (no multipart dependency); size-limited by
    # INGEST_UPLOAD_MAX_BYTES in the hardening middleware and here.
    require_scope(_request_scope(request), normalize_tenant_id(tenant))
    name = Path(str(filename or "")).name
    suffix = Path(name).suffix.lower()
    if suffix not in _UPLOAD_SUFFIXES:
        raise HTTPException(status_code=400, detail="unsupported file type (pdf, docx, xlsx, pptx, csv)")
    safe_name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)[-120:] or f"upload{suffix}"
    tenant_segment = re.sub(r"[^A-Za-z0-9_.-]", "_", normalize_tenant_id(tenant))
    upload_root = _upload_dir()
    _prune_old_uploads(upload_root)
    target_dir = upload_root / tenant_segment / time.strftime("%Y%m%d")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{uuid.uuid4().hex[:12]}_{safe_name}"
    partial = target.with_name(f".{target.name}.part")
    limit = security_middleware.max_upload_bytes()
    digest = hashlib.sha256()
    size = 0
    head = b""
    try:
        with partial.open("wb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(status_code=413, detail="file too large")
                if len(head) < 8:
                    head += chunk[: 8 - len(head)]
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="empty file")
        signatures = _UPLOAD_SUFFIXES[suffix]
        if signatures and not any(head.startswith(sig) for sig in signatures):
            raise HTTPException(status_code=400, detail="file content does not match its extension")
        os.replace(partial, target)
    finally:
        if partial.exists():
            partial.unlink()
    return {"path": str(target), "filename": name, "bytes": size, "sha256": digest.hexdigest()}


def _job_in_scope(job: Dict[str, Any], scope) -> bool:
    return scope is None or str(job.get("expected_tenant") or "default") in scope


@router.get("/admin/ingestion/jobs")
def ingestion_jobs_list(request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    scope = _request_scope(request)
    return {"jobs": [job for job in ingestion_jobs.list_jobs() if _job_in_scope(job, scope)]}


@router.get("/admin/ingestion/jobs/{job_id}")
def ingestion_job_status(job_id: str, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    job = ingestion_jobs.get_job(job_id)
    if job is None or not _job_in_scope(job, _request_scope(request)):
        raise HTTPException(status_code=404, detail="job not found")
    return job


# --- Document deletion and promotion to the served collection (stage 15) -------


class DocumentDeleteRequest(BaseModel):
    tenant_id: str = Field(..., max_length=128)
    source_docs: List[str] = Field(..., min_length=1, max_length=500)
    # None: the served (production) collection; otherwise a staging collection.
    collection: Optional[str] = Field(None, max_length=128)
    execute: bool = False


class PromoteRequest(BaseModel):
    collection: str = Field(..., max_length=128)
    tenant_id: str = Field(..., max_length=128)
    execute: bool = False


@router.post("/admin/ingestion/documents/delete")
def ingestion_delete_documents(req: DocumentDeleteRequest, request: Request = None,
                               _admin_auth: None = Depends(require_admin_auth)):
    # Dry run unless execute=true: the report lists the chunks per document.
    from webapi import collection_admin

    scope = _request_scope(request)
    tenant = normalize_tenant_id(req.tenant_id)
    require_scope(scope, tenant)
    try:
        report = collection_admin.delete_documents(req.collection, tenant=tenant, source_docs=req.source_docs,
                                                   execute=req.execute)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if req.execute:
        _audit_admin(scope, "documents_deleted", tenant_id=tenant, collection=report["collection"],
                     documents=sorted(report["documents"]), chunks=report["chunks"])
    return report


@router.post("/admin/ingestion/promote")
def ingestion_promote(req: PromoteRequest, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    # Staging -> served collection for one tenant's documents (dry run unless execute=true).
    from webapi import collection_admin

    scope = _request_scope(request)
    tenant = normalize_tenant_id(req.tenant_id)
    require_scope(scope, tenant)
    try:
        report = collection_admin.promote_staging(req.collection, tenant=tenant, execute=req.execute)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if req.execute:
        _audit_admin(scope, "collection_promoted", tenant_id=tenant, collection=req.collection,
                     documents=report["documents"], chunks=report["chunks"])
    return report
