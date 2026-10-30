"""Records of questions and answers, and what people judged of them (S3/S4).

  GET  /admin/records/level     the record level (default, ceiling, global, per tenant)
  POST /admin/records/level     set the level for all or one tenant, with a reason (never above RECORD_LEVEL_MAX)
  GET  /admin/records           the records, newest first, with feedback and judgement joined in (filters)
  GET  /admin/records/stats     counts for the dashboard
  GET  /admin/records/{id}      one record
  POST /admin/records/{id}/review   a person's judgement: correct, partly, wrong, and why
  POST /admin/records/purge     apply the retention periods (a dry run unless told otherwise); installation administrator only

Reading what people asked is itself an event: every read of the list or of a record is written to the audit log.
A tenant administrator sees the records of their tenants only.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from rag_core import llm_usage, records
from webapi import admin_common
from webapi.admin_auth import require_admin_auth, require_global, require_scope

router = APIRouter()


class LevelRequest(BaseModel):
    level: Optional[str] = Field(None, max_length=20)  # None: remove the setting (the global level or the default applies)
    reason: str = Field(..., min_length=1, max_length=300)
    tenant_id: Optional[str] = Field(None, max_length=64)


class ReviewRequest(BaseModel):
    verdict: str = Field(..., max_length=20)
    reviewer: str = Field(..., max_length=64)
    reasons: List[str] = Field(default_factory=list, max_length=6)
    note: str = Field("", max_length=1000)
    expected_answer: str = Field("", max_length=2000)


class PurgeRequest(BaseModel):
    dry_run: bool = True
    action: Optional[str] = Field(None, max_length=20)


def _in_scope(record: Optional[dict], scope) -> dict:
    # A record of another tenant is "not found", the same as one that does not exist.
    if record is None or (scope is not None and record.get("tenant_id") not in scope):
        raise HTTPException(status_code=404, detail="record not found")
    return record


@router.get("/admin/records/level")
def get_level(request: Request, _admin_auth: None = Depends(require_admin_auth)):
    scope = admin_common.request_scope(request)
    overview = records.level_overview()
    if scope is not None:
        overview["tenants"] = {t: v for t, v in overview["tenants"].items() if t in scope}
    return overview


@router.post("/admin/records/level")
def set_level(req: LevelRequest, request: Request, _admin_auth: None = Depends(require_admin_auth)):
    scope = admin_common.request_scope(request)
    if req.tenant_id:
        require_scope(scope, req.tenant_id)
    else:
        require_global(scope)
    try:
        change = records.set_level(req.level, by=admin_common.admin_label(scope), reason=req.reason, tenant_id=req.tenant_id)
    except records.RecordError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001 - runtime_settings errors are the caller's input
        if exc.__class__.__name__ in ("SettingsError", "Conflict"):
            raise HTTPException(status_code=400, detail=str(exc))
        raise
    admin_common.audit_admin(scope, "records_level", level=req.level, tenant_id=req.tenant_id, rev=change["rev"])
    return {"change": change, **records.level_overview()}


@router.get("/admin/records/stats")
def record_stats(request: Request, days: int = 7, _admin_auth: None = Depends(require_admin_auth)):
    scope = admin_common.request_scope(request)
    return records.stats(max(1, min(days, 400)), tenants=admin_common.allowed_tenants(scope))


@router.get("/admin/records")
def list_records(
    request: Request, days: int = 7, tenant_id: Optional[str] = None, answer_mode: Optional[str] = None,
    verdict: Optional[str] = None, feedback: Optional[str] = None, reviewed: Optional[bool] = None,
    abstained: Optional[bool] = None, text: Optional[str] = None, limit: int = 50, offset: int = 0,
    _admin_auth: None = Depends(require_admin_auth),
):
    scope = admin_common.request_scope(request)
    if tenant_id:
        require_scope(scope, tenant_id)
    found = records.list_records(
        days=max(1, min(days, 400)), tenants=admin_common.allowed_tenants(scope), tenant_id=tenant_id, answer_mode=answer_mode,
        verdict=verdict, feedback=feedback, reviewed=reviewed, abstained=abstained, text=text, limit=limit, offset=offset,
    )
    admin_common.audit_admin(scope, "records_listed", days=days, tenant_id=tenant_id, returned=len(found["records"]))
    return found


@router.post("/admin/records/purge")
def purge(req: PurgeRequest, request: Request, _admin_auth: None = Depends(require_admin_auth)):
    scope = admin_common.request_scope(request)
    require_global(scope)
    try:
        result = {"records": records.purge(dry_run=req.dry_run, action=req.action), "llm_usage": llm_usage.purge(dry_run=req.dry_run)}
    except records.RecordError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    admin_common.audit_admin(scope, "records_purge", dry_run=req.dry_run, expiry_action=result["records"]["action"],
                             anonymized=result["records"]["records_anonymized"], files_deleted=result["records"]["files_deleted"])
    return result


@router.get("/admin/records/{record_id}")
def get_record(record_id: str, request: Request, _admin_auth: None = Depends(require_admin_auth)):
    scope = admin_common.request_scope(request)
    record = _in_scope(records.get_record(record_id), scope)
    admin_common.audit_admin(scope, "record_viewed", record_id=record_id, tenant_id=record.get("tenant_id"))
    return record


@router.post("/admin/records/{record_id}/review")
def review_record(record_id: str, req: ReviewRequest, request: Request, _admin_auth: None = Depends(require_admin_auth)):
    scope = admin_common.request_scope(request)
    record = _in_scope(records.get_record(record_id), scope)
    name = admin_common.actor_name(req.reviewer)
    try:
        row = records.review(record_id, verdict=req.verdict, reviewer=name, reasons=req.reasons, note=req.note,
                             expected_answer=req.expected_answer)
    except records.RecordError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    admin_common.audit_admin(scope, "record_reviewed", record_id=record_id, verdict=req.verdict, tenant_id=record.get("tenant_id"))
    return row
