"""Document ingestion UI, job status, upload, deletion and promotion (moved from main.py, S1).

All under /admin/ingestion. The upload pruning helpers live here; main.py re-exports them."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

import config
from rag_core import ingest_input_guard, ingest_settings, runtime_settings
from rag_core.retrieval import normalize_tenant_id
from webapi import admin_common
from webapi import ingestion_jobs
from webapi import mail_ingestion_jobs
from webapi import security_middleware
from webapi.admin_auth import require_admin_auth, require_global, require_scope

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
    # Per-job options (PDF only): how a PDF is split (auto, policy, faq, procedure) and whether pages without a
    # text layer are OCRed (null: the INGEST_PDF_OCR setting).
    doc_type: Optional[str] = None
    ocr: Optional[bool] = None


@router.get("/admin/ingestion", response_class=HTMLResponse)
def ingestion_page(_admin_auth: None = Depends(require_admin_auth)):
    path = _STATIC_DIR / "ingestion.html"
    try:
        return HTMLResponse(_admin_page_html(path))
    except Exception:
        logging.exception("ingestion page unavailable")
        raise HTTPException(status_code=500, detail="ingestion page unavailable")


def _check_upload_tenant(inputs: List[str], expected_tenant: Optional[str]) -> None:
    """Uploads and source mirrors belong to one tenant: an administrator of one tenant must not pull another
    tenant's documents into their own collection, directly or through an ancestor folder (see ingest_input_guard)."""
    try:
        upload_root: Optional[Path] = _upload_dir()
    except HTTPException:
        upload_root = None  # not usable for uploads: the guard still protects the default location
    try:
        ingest_input_guard.check_paths(inputs, expected_tenant, upload=upload_root)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))


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
    _check_upload_tenant(inputs, req.expected_tenant)
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
    if not str(req.collection or "").strip():
        raise HTTPException(status_code=400, detail="collection is required")
    if ingestion_jobs.is_production_collection(req.collection):
        raise HTTPException(status_code=400, detail="refusing production/default collection")
    try:
        ingestion_jobs.normalize_job_options(req.doc_type, req.ocr)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _check_upload_tenant(inputs, req.expected_tenant)
    # Audited once the request is accepted: a refused one is not an execution.
    if req.execute:
        _audit_admin(scope, "ingestion_execute", tenant_id=req.expected_tenant, collection=req.collection,
                     files=len(inputs), async_job=bool(req.async_job))
    try:
        if req.async_job:
            queued = ingestion_jobs.submit_raw_document_job(
                inputs,
                expected_tenant=req.expected_tenant,
                collection=req.collection,
                execute=bool(req.execute),
                doc_type=req.doc_type,
                ocr=req.ocr,
            )
            return JSONResponse(status_code=202, content=queued)
        return ingestion_jobs.run_raw_document_job(
            inputs,
            expected_tenant=req.expected_tenant,
            collection=req.collection,
            execute=bool(req.execute),
            doc_type=req.doc_type,
            ocr=req.ocr,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class MailIngestionRequest(BaseModel):
    inputs: List[str]
    expected_tenant: str
    collection: str
    execute: bool = False
    # Ignored when execute=true: a mail write is always queued (202; poll the job). A dry run answers directly.
    async_job: bool = False
    # The mailbox the files were exported from (its address as the owner-side participant): used to tell the
    # owner's own messages from other participants when the access labels are derived.
    mailbox_owner: Optional[str] = Field(None, max_length=254)


@router.post("/admin/ingestion/mail")
def ingestion_mail(
    req: MailIngestionRequest,
    request: Request = None,
    _admin_auth: None = Depends(require_admin_auth),
):
    inputs = [str(p) for p in (req.inputs or []) if str(p).strip()]
    tenant = str(req.expected_tenant or "").strip()
    if not inputs or not tenant:
        raise HTTPException(status_code=400, detail="inputs and expected_tenant are required")
    scope = _request_scope(request)
    require_scope(scope, tenant)
    if ingestion_jobs.is_production_collection(req.collection):
        raise HTTPException(status_code=400, detail="refusing production/default collection")
    # Mail may contain confidential per-person data. Unlike generic document
    # ingestion, the mail endpoint is restricted to this tenant's upload tree.
    # This prevents a scoped tenant admin from importing another tenant's
    # files from a shared, otherwise-allowed filesystem root.
    upload_root = _upload_dir()
    own_segment = _tenant_segment(tenant)
    for raw in inputs:
        try:
            resolved = ingestion_jobs.resolve_ingest_input(raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        if not resolved.is_relative_to(upload_root):
            raise HTTPException(status_code=403, detail="mail input must be under tenant upload directory")
        parts = resolved.relative_to(upload_root).parts
        if len(parts) != 3 or parts[0] != own_segment or not re.fullmatch(r"\d{8}", parts[1]):
            raise HTTPException(status_code=403, detail="mail input belongs to another tenant or invalid upload layout")
    try:
        # Validate the entire batch before audit/queueing: no write occurs here.
        mail_ingestion_jobs._validate(inputs, tenant, req.collection)
        if req.execute:
            _audit_admin(scope, "mail_ingestion_execute", tenant_id=tenant,
                         collection=req.collection, files=len(inputs), async_job=True)
        owner = req.mailbox_owner or None
        if req.execute or req.async_job:  # a write is always asynchronous: poll /admin/ingestion/jobs/{job_id}
            queued = mail_ingestion_jobs.submit_mail_job(
                inputs, expected_tenant=tenant, collection=req.collection, execute=req.execute,
                mailbox_owner=owner)
            return JSONResponse(status_code=202, content=queued)
        return mail_ingestion_jobs.run_mail_job(
            inputs, expected_tenant=tenant, collection=req.collection, execute=False, mailbox_owner=owner)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class MailDeleteRequest(BaseModel):
    tenant_id: str = Field(..., max_length=128)
    collection: str = Field(..., max_length=128)
    source_doc: str = Field("", max_length=300)
    message_id: str = Field("", max_length=998)
    execute: bool = False


@router.post("/admin/ingestion/mail/delete")
def ingestion_mail_delete(req: MailDeleteRequest, request: Request = None,
                          _admin_auth: None = Depends(require_admin_auth)):
    """Delete one ingested mail (by source_doc or Message-ID) from a staging collection.

    A dry run (default) returns the plan. execute=true deletes its chunks (vector + keyword, by source_doc),
    records a tombstone, clears the tenant's cached answers, then makes the mail state forget the message.
    The report lists the mails that quote it (dependent_source_docs) and those it superseded; re-ingest them."""
    from webapi import collection_admin

    scope = _request_scope(request)
    tenant = str(req.tenant_id or "").strip()
    require_scope(scope, tenant)
    if not (req.source_doc.strip() or req.message_id.strip()):
        raise HTTPException(status_code=400, detail="source_doc or message_id is required")
    if ingestion_jobs.is_production_collection(req.collection):
        raise HTTPException(status_code=400, detail="refusing production/default collection")
    try:
        plan = mail_ingestion_jobs.delete_mail(tenant, req.collection, source_doc=req.source_doc,
                                               message_id=req.message_id, commit=False)
        report: Dict[str, Any] = {
            "executed": bool(req.execute), "tenant": tenant, "collection": req.collection,
            "source_doc": plan["source_doc"], "found": plan["found"], "ids": len(plan["ids"]),
            "dependent_source_docs": plan["dependent_source_docs"],
            "superseded_source_docs": plan["superseded_source_docs"],
        }
        if req.execute:
            deleted = collection_admin.delete_documents(req.collection, tenant=tenant,
                                                        source_docs=[plan["source_doc"]], execute=True,
                                                        reason="mail_delete")
            committed = mail_ingestion_jobs.delete_mail(tenant, req.collection, source_doc=plan["source_doc"],
                                                        commit=True)
            report.update({"chunks_deleted": deleted["chunks"],
                           "keyword_rows_removed": deleted.get("keyword_rows_removed", 0),
                           "tombstoned": deleted.get("tombstoned", 0), "committed": committed["committed"]})
            _audit_admin(scope, "mail_deleted", tenant_id=tenant, collection=req.collection,
                         source_doc=plan["source_doc"], chunks=deleted["chunks"])
        return report
    except collection_admin.ConversationRedactionError as exc:
        raise HTTPException(status_code=500, detail="conversation redaction failed") from exc
    except ingestion_jobs.MaintenanceBusy as exc:
        raise HTTPException(status_code=503, detail=str(exc), headers={"Retry-After": "30"})
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


_UPLOAD_SUFFIXES = {".pdf": (b"%PDF-",), ".docx": (b"PK\x03\x04",), ".xlsx": (b"PK\x03\x04",),
                    ".pptx": (b"PK\x03\x04",), ".csv": (), ".tsv": (),
                    ".eml": (), ".xlsm": (b"PK\x03\x04",)}
# Parts every genuine Office Open XML package of that type contains.
_OOXML_REQUIRED = {".docx": "word/document.xml", ".xlsx": "xl/workbook.xml", ".xlsm": "xl/workbook.xml", ".pptx": "ppt/presentation.xml"}
_HEAD_BYTES = 8192
_DEFAULT_TENANT_QUOTA_BYTES = 5 * 1024 ** 3


def _tenant_quota_bytes() -> int:
    """INGEST_UPLOAD_TENANT_QUOTA_BYTES: stored uploads per tenant (default 5 GiB; 0 = unlimited)."""
    return max(0, _env_int("INGEST_UPLOAD_TENANT_QUOTA_BYTES", _DEFAULT_TENANT_QUOTA_BYTES))


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, "")).strip() or default)
    except ValueError:
        return default


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


def _tenant_segment(tenant: Optional[str]) -> str:
    # Folder name for a tenant. Dots stay allowed inside a name, but a name made
    # only of dots (".", "..") would resolve to the upload root or its parent.
    segment = re.sub(r"[^A-Za-z0-9_.-]", "_", normalize_tenant_id(tenant))[:128]
    if not segment.strip("."):
        raise HTTPException(status_code=400, detail="invalid tenant")
    return segment


_UPLOAD_PRUNE = {"last": 0.0}
_UPLOAD_PRUNE_LOCK = threading.Lock()
_UPLOAD_ACTIVE = {"n": 0}  # uploads in flight; only touched on the event loop
_PART_MAX_AGE_SECONDS = 3600


def _prune_old_uploads(directory: Path) -> int:
    # INGEST_UPLOAD_RETENTION_DAYS (default 30, 0 = keep): uploaded files
    # older than that are removed, at most once an hour. Only files named
    # like uploads ("<12 hex>_<name>") and their emptied day folders.
    # Interrupted uploads (".<name>.part") older than an hour go regardless of
    # the retention setting.
    try:
        days = float(os.getenv("INGEST_UPLOAD_RETENTION_DAYS", "") or 30)
    except ValueError:
        days = 30.0
    now = time.time()
    with _UPLOAD_PRUNE_LOCK:
        if now - _UPLOAD_PRUNE["last"] < 3600:
            return 0
        _UPLOAD_PRUNE["last"] = now
    removed = 0
    for path in directory.glob("*/*/*"):
        try:
            if not path.is_file():
                continue
            age = now - path.stat().st_mtime
            stale_part = path.name.startswith(".") and path.name.endswith(".part") and age > _PART_MAX_AGE_SECONDS
            stale_upload = days > 0 and re.fullmatch(r"[0-9a-f]{12}_.+", path.name) and age > days * 86400
            if stale_part or stale_upload:
                path.unlink()
                removed += 1
        except OSError:
            continue
    for day_dir in directory.glob("*/*"):
        try:
            if day_dir.is_dir() and not any(day_dir.iterdir()):
                day_dir.rmdir()
        except OSError:
            continue
    return removed


def _dir_bytes(directory: Path) -> int:
    total = 0
    if directory.is_dir():
        for path in directory.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except OSError:
                continue
    return total


def _validate_upload(path: Path, suffix: str, head: bytes) -> None:
    """Reject files that only pretend to be what their extension says."""
    signatures = _UPLOAD_SUFFIXES[suffix]
    if signatures and not any(head.startswith(sig) for sig in signatures):
        raise HTTPException(status_code=400, detail="file content does not match its extension")
    if suffix in {".csv", ".tsv", ".eml"}:
        if b"\x00" in head:
            raise HTTPException(status_code=400, detail="file content does not match its extension")
        if suffix == ".eml" and not re.search(rb"(?im)^(from|to|date|subject|message-id):", head):
            raise HTTPException(status_code=400, detail="invalid EML headers")
        return
    required = _OOXML_REQUIRED.get(suffix)
    if not required:
        return
    max_entries = _env_int("INGEST_UPLOAD_MAX_ZIP_ENTRIES", 5000)
    max_total = _env_int("INGEST_UPLOAD_MAX_UNCOMPRESSED_BYTES", 500 * 1024 * 1024)
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = {info.filename for info in infos}
            if len(infos) > max_entries:
                raise HTTPException(status_code=400, detail="too many entries in the document package")
            for name in names:
                if name.startswith(("/", "\\")) or ".." in re.split(r"[\\/]", name):
                    raise HTTPException(status_code=400, detail="invalid entry name in the document package")
            if sum(info.file_size for info in infos) > max_total:
                raise HTTPException(status_code=400, detail="document package expands too large")
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="file content does not match its extension")
    if "[Content_Types].xml" not in names or required not in names:
        raise HTTPException(status_code=400, detail="file content does not match its extension")


def _prepare_upload_dir(upload_root: Path, tenant_segment: str) -> tuple[Path, int]:
    # Blocking filesystem work, run off the event loop.
    _prune_old_uploads(upload_root)
    target_dir = upload_root / tenant_segment / time.strftime("%Y%m%d")
    if not target_dir.resolve().is_relative_to(upload_root):
        raise HTTPException(status_code=400, detail="invalid tenant")
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir, _committed_bytes(upload_root / tenant_segment)


def _is_partial(path: Path) -> bool:
    return path.name.startswith(".") and path.name.endswith(".part")


def _committed_bytes(directory: Path) -> int:
    """Stored uploads of a tenant: finished files only (uploads still in flight are counted when they commit)."""
    total = 0
    if directory.is_dir():
        for path in directory.rglob("*"):
            try:
                if path.is_file() and not _is_partial(path):
                    total += path.stat().st_size
            except OSError:
                continue
    return total


def _commit_upload(partial: Path, target: Path, tenant_dir: Path, size: int, quota: int) -> None:
    """Move a received upload into place, re-checking the tenant quota under a per-tenant lock.

    The quota seen when the upload started is stale once several uploads of the tenant run at once; the
    re-check and the move happen together under an exclusive file lock (shared by every worker process),
    so concurrent uploads cannot jointly exceed the quota. Blocking: run off the event loop."""
    import fcntl

    with open(tenant_dir / ".quota.lock", "a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            if quota > 0 and _committed_bytes(tenant_dir) + size > quota:
                raise HTTPException(status_code=413, detail="tenant upload quota exceeded")
            os.replace(partial, target)
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _discard_partial(partial: Path) -> None:
    try:
        partial.unlink()
    except FileNotFoundError:
        pass


def _public_path(target: Path) -> str:
    # Paths under BASE_DIR are returned relative to it (no host layout in the
    # response); ingestion resolves relative inputs against BASE_DIR again.
    try:
        return str(target.relative_to(Path(config.BASE_DIR).resolve()))
    except ValueError:
        return str(target)


@router.put("/admin/ingestion/upload")
async def ingestion_upload(
    request: Request,
    filename: str,
    tenant: Optional[str] = None,
    _admin_auth: None = Depends(require_admin_auth),
):
    # Raw request body (no multipart dependency); size-limited by
    # INGEST_UPLOAD_MAX_BYTES in the hardening middleware and here.
    scope = _request_scope(request)
    tenant_id = normalize_tenant_id(tenant)
    require_scope(scope, tenant_id)
    # Limits changed in the console (by any worker process) apply to this upload. File IO: off the event loop.
    await asyncio.to_thread(ingest_settings.sync_environ)
    name = Path(str(filename or "")).name
    suffix = Path(name).suffix.lower()
    if suffix not in _UPLOAD_SUFFIXES:
        raise HTTPException(status_code=400, detail="unsupported file type (pdf, docx, xlsx, xlsm, pptx, csv, tsv, eml)")
    safe_name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)[-120:] or f"upload{suffix}"
    tenant_segment = _tenant_segment(tenant)
    upload_root = await asyncio.to_thread(_upload_dir)
    max_active = _env_int("INGEST_UPLOAD_MAX_CONCURRENT", 4)
    if max_active > 0 and _UPLOAD_ACTIVE["n"] >= max_active:
        raise HTTPException(status_code=429, detail="too many uploads in progress")
    _UPLOAD_ACTIVE["n"] += 1
    try:
        return await _receive_upload(
            request, scope, tenant_id, name, safe_name, suffix, tenant_segment, upload_root
        )
    finally:
        _UPLOAD_ACTIVE["n"] -= 1


async def _receive_upload(request, scope, tenant_id, name, safe_name, suffix, tenant_segment, upload_root):
    target_dir, used = await asyncio.to_thread(_prepare_upload_dir, upload_root, tenant_segment)
    target = target_dir / f"{uuid.uuid4().hex[:12]}_{safe_name}"
    partial = target.with_name(f".{target.name}.part")
    limit = security_middleware.max_upload_bytes()
    quota = _tenant_quota_bytes()
    digest = hashlib.sha256()
    size = 0
    head = b""
    try:
        with partial.open("wb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(status_code=413, detail="file too large")
                if quota > 0 and used + size > quota:
                    raise HTTPException(status_code=413, detail="tenant upload quota exceeded")
                if len(head) < _HEAD_BYTES:
                    head += chunk[: _HEAD_BYTES - len(head)]
                digest.update(chunk)
                await asyncio.to_thread(out.write, chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="empty file")
        await asyncio.to_thread(_validate_upload, partial, suffix, head)
        await asyncio.to_thread(_commit_upload, partial, target, upload_root / tenant_segment, size, quota)
    finally:
        await asyncio.to_thread(_discard_partial, partial)
    await asyncio.to_thread(_audit_admin, scope, "ingestion_upload", tenant_id=tenant_id, filename=name, bytes=size,
                            sha256=digest.hexdigest())
    return {"path": _public_path(target), "filename": name, "bytes": size, "sha256": digest.hexdigest()}


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
    except collection_admin.ConversationRedactionError as exc:
        raise HTTPException(status_code=500, detail="conversation redaction failed") from exc
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
    except collection_admin.ConversationRedactionError as exc:
        raise HTTPException(status_code=500, detail="conversation redaction failed") from exc
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


# --- ingestion settings (upload and conversion limits, OCR, tables, headings, preprocessing, chunk sizes) -----------


class IngestSettingChange(BaseModel):
    name: str = Field(..., max_length=80)
    value: Optional[Any] = None
    reset: bool = False  # remove the override: the deployment's environment (or the default) applies again
    reason: str = Field("", max_length=300)
    actor: str = Field("", max_length=64)
    expected_rev: Optional[int] = None


@router.get("/admin/ingestion/settings")
def ingestion_settings_list(request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    scope = _request_scope(request)
    ingest_settings.sync_environ()
    return {
        "rev": runtime_settings.revision(),
        "settings": ingest_settings.effective(),
        "groups": list(ingest_settings.GROUPS),
        "history": ingest_settings.history(30),
        "can_edit": scope is None,  # the settings are installation-wide: only the installation administrator changes them
    }


@router.post("/admin/ingestion/settings")
def ingestion_settings_change(req: IngestSettingChange, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    scope = _request_scope(request)
    require_global(scope)
    who = admin_common.actor_identity(request, req.actor)["name"]
    try:
        if req.reset:
            record = ingest_settings.reset(req.name, by=who, reason=req.reason, expected_rev=req.expected_rev)
        else:
            if req.value is None:
                raise HTTPException(status_code=400, detail="value is required (or reset=true)")
            record = ingest_settings.set_value(req.name, req.value, by=who, reason=req.reason, expected_rev=req.expected_rev)
    except runtime_settings.Conflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except runtime_settings.SettingsError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if ingest_settings.definition(req.name).restart == "workers":
        from rag_core.document_converters import isolated

        isolated.shutdown()  # idle conversion workers start again with the new limits
    _audit_admin(scope, "ingestion_setting_changed", setting=req.name, before=record.get("before"), after=record.get("after"),
                 actor=who, reason=req.reason)
    return {"ok": True, "rev": record["rev"], "name": req.name, "before": record.get("before"), "after": record.get("after")}
