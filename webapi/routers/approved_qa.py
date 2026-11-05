"""Approved QA from the console: list, edit, approve, reject, delete, import (CSV of the Excel template), export.

The rules (approval needs a citation, an edited approved answer goes back to draft, a different person approves when
APPROVED_QA_REQUIRE_APPROVER is on, history, one writer at a time) are in rag_core/approved_qa_store.py.
A tenant administrator reaches the records of their own tenants only; the installation administrator all of them."""
from __future__ import annotations

import base64
import binascii
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

import config
from rag_core import approved_qa_store as store
from webapi import admin_common
from webapi.admin_auth import require_admin_auth, require_scope

router = APIRouter()

_request_scope = admin_common.request_scope
_audit_admin = admin_common.audit_admin


class RecordIn(BaseModel):
    tenant_id: str = Field("default", max_length=64)
    qa_id: Optional[str] = Field(None, max_length=100)
    question: str = Field(..., max_length=store.MAX_QUESTION_CHARS + 50)
    approved_answer: str = Field(..., max_length=store.MAX_ANSWER_CHARS + 500)
    aliases: List[str] = Field(default_factory=list, max_length=50)
    tags: List[str] = Field(default_factory=list, max_length=50)
    citations: List[Dict[str, Any]] = Field(default_factory=list, max_length=20)
    doc_version: Optional[str] = Field(None, max_length=100)
    reason: str = Field("", max_length=300)
    actor: str = Field("", max_length=64)
    expected_updated_at: Optional[str] = Field(None, max_length=64)


class StatusIn(BaseModel):
    tenant_id: str = Field("default", max_length=64)
    qa_id: str = Field(..., max_length=100)
    status: str = Field(..., max_length=20)
    reason: str = Field("", max_length=300)
    actor: str = Field("", max_length=64)


class DeleteIn(BaseModel):
    tenant_id: str = Field("default", max_length=64)
    qa_id: str = Field(..., max_length=100)
    reason: str = Field("", max_length=300)
    actor: str = Field("", max_length=64)


class ImportIn(BaseModel):
    tenant_id: str = Field("default", max_length=64)  # the tenant of rows that leave the tenant column empty
    content: Optional[str] = None  # the CSV as text, or
    content_base64: Optional[str] = None  # its bytes (a Shift_JIS file keeps its encoding)
    apply: bool = False  # false: only a report
    on_conflict: str = Field("skip", max_length=10)
    reason: str = Field("", max_length=300)
    actor: str = Field("", max_length=64)


def _error(exc: Exception) -> HTTPException:
    text = str(exc)
    if isinstance(exc, store.Conflict):
        return HTTPException(status_code=409, detail=text)
    if text == "no such record":
        return HTTPException(status_code=404, detail=text)
    return HTTPException(status_code=400, detail=text)


def _tenants(scope, tenant_id: Optional[str]) -> Optional[Set[str]]:
    """The tenants a list may show: the one asked for (checked against the scope), else the whole scope."""
    if tenant_id:
        require_scope(scope, tenant_id)
        return {tenant_id}
    return None if scope is None else set(scope)


@router.get("/admin/approved-qa")
def approved_qa_list(
    tenant_id: Optional[str] = Query(None, max_length=64), status: Optional[str] = Query(None, max_length=20),
    q: str = Query("", max_length=200), limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    request: Request = None, _admin_auth: None = Depends(require_admin_auth),
):
    scope = _request_scope(request)
    if status and status not in store.STATUSES:
        raise HTTPException(status_code=400, detail=f"the status is one of: {', '.join(store.STATUSES)}")
    result = store.list_records(tenants=_tenants(scope, tenant_id), status=status or None, query=q, limit=limit, offset=offset)
    return {**result, "enabled": bool(getattr(config, "APPROVED_QA_ENABLED", False)), "require_approver": store.require_approver(),
            "can_see_all": scope is None}


@router.get("/admin/approved-qa/health")
def approved_qa_health(request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    scope = _request_scope(request)
    full = store.health()
    if scope is not None:  # the error texts quote questions: only the installation administrator sees them
        own = store.list_records(tenants=set(scope), limit=1)
        return {"total": sum(own["counts"].values()), "by_status": own["counts"], "errors": [], "unreadable_lines": 0,
                "enabled": full["enabled"], "path_is_default": full["path_is_default"], "detail_hidden": True}
    return {**full, "detail_hidden": False}


@router.get("/admin/approved-qa/record")
def approved_qa_record(tenant_id: str = Query("default", max_length=64), qa_id: str = Query(..., max_length=100),
                       request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    require_scope(_request_scope(request), tenant_id)
    try:
        record = store.get(tenant_id, qa_id)
        rows = store.history(tenant_id, qa_id, 30)
    except store.StoreError as exc:
        raise _error(exc)
    if record is None:
        raise HTTPException(status_code=404, detail="no such record")
    return {"record": record, "history": [{k: v for k, v in r.items() if k not in ("before", "after")} | {
        "status_before": (r.get("before") or {}).get("status"), "status_after": (r.get("after") or {}).get("status")} for r in rows]}


@router.post("/admin/approved-qa/save")
def approved_qa_save(body: RecordIn, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    scope = _request_scope(request)
    require_scope(scope, body.tenant_id)
    who = admin_common.actor_identity(request, body.actor)
    try:
        record = store.save(body.model_dump(exclude={"reason", "actor", "expected_updated_at"}), by=who, reason=body.reason,
                            expected_updated_at=body.expected_updated_at)
    except store.StoreError as exc:
        raise _error(exc)
    _audit_admin(scope, "approved_qa_save", tenant_id=body.tenant_id, qa_id=record["qa_id"], status=record["status"], actor=who["name"])
    return record


@router.post("/admin/approved-qa/status")
def approved_qa_status(body: StatusIn, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    scope = _request_scope(request)
    require_scope(scope, body.tenant_id)
    who = admin_common.actor_identity(request, body.actor)
    try:
        record = store.set_status(body.tenant_id, body.qa_id, body.status, by=who, reason=body.reason)
    except store.StoreError as exc:
        raise _error(exc)
    _audit_admin(scope, "approved_qa_status", tenant_id=body.tenant_id, qa_id=body.qa_id, status=body.status, actor=who["name"])
    return record


@router.post("/admin/approved-qa/delete")
def approved_qa_delete(body: DeleteIn, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    scope = _request_scope(request)
    require_scope(scope, body.tenant_id)
    who = admin_common.actor_identity(request, body.actor)
    try:
        store.delete(body.tenant_id, body.qa_id, by=who, reason=body.reason)
    except store.StoreError as exc:
        raise _error(exc)
    _audit_admin(scope, "approved_qa_delete", tenant_id=body.tenant_id, qa_id=body.qa_id, actor=who["name"])
    return {"ok": True}


@router.post("/admin/approved-qa/import")
def approved_qa_import(body: ImportIn, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    scope = _request_scope(request)
    require_scope(scope, body.tenant_id)
    if (body.content is None) == (body.content_base64 is None):
        raise HTTPException(status_code=400, detail="send either content or content_base64")
    try:
        raw: Any = body.content if body.content is not None else base64.b64decode(body.content_base64 or "", validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="content_base64 is not valid base64")
    who = admin_common.actor_identity(request, body.actor) if body.apply else None
    try:
        result = store.import_rows(raw, default_tenant=body.tenant_id, allowed_tenants=None if scope is None else set(scope), apply=body.apply,
                                   by=who or "", reason=body.reason, on_conflict=body.on_conflict)
    except store.StoreError as exc:
        raise _error(exc)
    if body.apply:
        _audit_admin(scope, "approved_qa_import", tenant_id=body.tenant_id, counts=result["counts"], actor=who["name"] if who else "")
    return result


@router.get("/admin/approved-qa/export", response_class=PlainTextResponse)
def approved_qa_export(tenant_id: Optional[str] = Query(None, max_length=64), status: Optional[str] = Query(None, max_length=20),
                       request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    scope = _request_scope(request)
    text = store.export_jsonl(tenants=_tenants(scope, tenant_id), status=status or None)
    return PlainTextResponse(text, media_type="application/x-ndjson",
                             headers={"Content-Disposition": 'attachment; filename="approved_qa.jsonl"'})


@router.get("/admin/approved-qa/template", response_class=PlainTextResponse)
def approved_qa_template(_admin_auth: None = Depends(require_admin_auth)):
    return PlainTextResponse(store.template_csv(), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": 'attachment; filename="approved_qa_template.csv"'})
