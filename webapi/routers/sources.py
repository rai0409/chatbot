"""Document sources (5-4): the admin page and the source routes (moved from main.py, S1)."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from rag_core import change_ledger as source_changes, source_apply, source_registry, source_sync
from webapi import admin_common
from webapi.admin_auth import require_admin_auth, require_scope

router = APIRouter()

_STATIC_DIR = admin_common.STATIC_DIR
_request_scope = admin_common.request_scope
_audit_admin = admin_common.audit_admin
_admin_page_html = admin_common.admin_page_html


class SourceCreateRequest(BaseModel):
    source_id: str = Field(..., max_length=48)
    tenant_id: str = Field(..., max_length=64)
    kind: str = Field("folder", max_length=16)
    path: Optional[str] = Field(None, max_length=1024)
    options: Optional[Dict[str, Any]] = None
    schedule_minutes: int = 0
    collection: Optional[str] = Field(None, max_length=128)


class SourceApplyRequest(BaseModel):
    approval: Optional[str] = Field(None, max_length=64)


class SourceApproveRequest(BaseModel):
    plan_sha256: str = Field(..., max_length=64)


def _source_http_error(exc: Exception) -> HTTPException:
    text = str(exc)
    if "not found" in text:
        return HTTPException(status_code=404, detail=text)
    if "already exists" in text or "changed by someone else" in text:
        return HTTPException(status_code=409, detail=text)
    return HTTPException(status_code=400, detail=text)


_admin_label = admin_common.admin_label


def _owned_source(source_id: str, scope) -> Dict[str, Any]:
    # A source outside the administrator's tenants is "not found", the same as one that does not exist.
    try:
        record = source_registry.get(source_id)
    except source_registry.SourceError as exc:
        raise _source_http_error(exc)
    if scope is not None and record["tenant_id"] not in scope:
        raise HTTPException(status_code=404, detail=f"source '{source_id}' not found")
    return record


def _source_row(record: Dict[str, Any]) -> Dict[str, Any]:
    # Never the server path or the options of the source.
    row = {key: record[key] for key in ("source_id", "tenant_id", "kind", "collection", "schedule_minutes", "enabled", "state")}
    try:
        plan = source_apply.plan_source(record["source_id"])
        row["pending"], row["guard"] = plan["counts"], source_apply.guard(plan)
    except source_registry.SourceError:
        row["pending"], row["guard"] = None, None
    return row


@router.get("/admin/sources", response_class=HTMLResponse)
def sources_page(_admin_auth: None = Depends(require_admin_auth)):
    path = _STATIC_DIR / "sources.html"
    try:
        return HTMLResponse(_admin_page_html(path))
    except Exception:
        logging.exception("sources page unavailable")
        raise HTTPException(status_code=500, detail="sources page unavailable")


@router.get("/admin/sources/list")
def sources_list(request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    scope = _request_scope(request)
    return {"sources": [_source_row(r) for r in source_registry.list_sources()
                        if scope is None or r["tenant_id"] in scope]}


@router.post("/admin/sources")
def sources_create(req: SourceCreateRequest, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    scope = _request_scope(request)
    require_scope(scope, req.tenant_id)
    try:
        record = source_registry.create(
            req.source_id, tenant_id=req.tenant_id, kind=req.kind, path=req.path, options=req.options,
            schedule_minutes=req.schedule_minutes, collection=req.collection, created_by=_admin_label(scope),
        )
    except source_registry.SourceError as exc:
        raise _source_http_error(exc)
    _audit_admin(scope, "source_created", tenant_id=req.tenant_id, source_id=req.source_id, kind=req.kind)
    return _source_row(record)


@router.post("/admin/sources/{source_id}/scan")
def sources_scan(source_id: str, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    scope = _request_scope(request)
    record = _owned_source(source_id, scope)
    try:
        result = source_sync.scan_source(source_id)
    except Exception as exc:  # noqa: BLE001 - a connector failure is reported without its details
        if isinstance(exc, source_registry.SourceError):
            raise _source_http_error(exc)
        logging.exception("source scan failed source_id=%s", source_id)
        raise HTTPException(status_code=502, detail="the source could not be scanned")
    _audit_admin(scope, "source_scanned", tenant_id=record["tenant_id"], source_id=source_id,
                 counts=result.get("counts"), error=result.get("error"))
    if "error" in result:
        return {"error": result["error"], "run_id": result["run_id"]}
    return {"run_id": result["run_id"], "counts": result["counts"], "total_files": result["total_files"],
            "skipped": {k: (len(v) if isinstance(v, list) else v) for k, v in result["skipped"].items()},
            "name_conflicts": len(result["name_conflicts"])}


@router.get("/admin/sources/{source_id}/plan")
def sources_plan(source_id: str, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    _owned_source(source_id, _request_scope(request))
    try:
        plan = source_apply.plan_source(source_id)
    except source_registry.SourceError as exc:
        raise _source_http_error(exc)
    plan["guard"] = source_apply.guard(plan)
    for key in ("added", "modified", "deleted"):
        plan[key] = plan[key][:200]  # the rest is in the change ledger
    return plan


@router.post("/admin/sources/{source_id}/apply")
def sources_apply(source_id: str, req: SourceApplyRequest, request: Request = None,
                  _admin_auth: None = Depends(require_admin_auth)):
    scope = _request_scope(request)
    record = _owned_source(source_id, scope)
    try:
        result = source_apply.apply_source(source_id, approval=req.approval, actor=_admin_label(scope))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except source_registry.SourceError as exc:
        raise _source_http_error(exc)
    except Exception:  # noqa: BLE001
        logging.exception("source apply failed source_id=%s", source_id)
        raise HTTPException(status_code=502, detail="the changes could not be applied")
    _audit_admin(scope, "source_applied", tenant_id=record["tenant_id"], source_id=source_id,
                 status=result.get("status"), counts=result.get("counts"), reasons=result.get("reasons"))
    return result


@router.post("/admin/sources/{source_id}/approve")
def sources_approve(source_id: str, req: SourceApproveRequest, request: Request = None,
                    _admin_auth: None = Depends(require_admin_auth)):
    scope = _request_scope(request)
    record = _owned_source(source_id, scope)
    try:
        result = source_apply.approve(source_id, req.plan_sha256, actor=_admin_label(scope))
    except source_registry.SourceError as exc:
        raise _source_http_error(exc)
    _audit_admin(scope, "source_approved", tenant_id=record["tenant_id"], source_id=source_id, plan_sha256=req.plan_sha256)
    return result


@router.post("/admin/sources/{source_id}/{action}")
def sources_switch(source_id: str, action: str, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    if action not in {"enable", "disable", "remove"}:
        raise HTTPException(status_code=404, detail="unknown action")
    scope = _request_scope(request)
    record = _owned_source(source_id, scope)
    try:
        if action == "remove":
            source_registry.remove(source_id)
            result: Dict[str, Any] = {"removed": source_id}
        else:
            result = _source_row(source_registry.set_enabled(source_id, action == "enable"))
    except source_registry.SourceError as exc:
        raise _source_http_error(exc)
    _audit_admin(scope, f"source_{action}d" if action != "remove" else "source_removed",
                 tenant_id=record["tenant_id"], source_id=source_id)
    return result


@router.get("/admin/sources/{source_id}/changes")
def sources_changes(source_id: str, limit: int = Query(20, ge=1, le=200), request: Request = None,
                    _admin_auth: None = Depends(require_admin_auth)):
    _owned_source(source_id, _request_scope(request))
    try:
        entries = source_changes.read_entries(source_id, limit)
    except source_changes.LedgerError as exc:
        raise _source_http_error(exc)
    return {"entries": [{k: e.get(k) for k in ("seq", "at", "kind", "run_id", "summary")} for e in entries]}
