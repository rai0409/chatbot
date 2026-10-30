"""What the LLM was used for, how long it was down, how much may be used, and which model answers whom (S4, S6).

  GET  /admin/usage             calls, errors, tokens and cost added up by tenant, feature, profile, model, caller or location
  GET  /admin/usage/daily       the same for each day
  GET  /admin/usage/downtime    the time each breaker was open, and how much of it fell in business hours
  GET  /admin/limits            the daily limits in force and today's use against them (rag_core/llm_quota.py)
  POST /admin/limits            set a limit for all or for one tenant, with a reason (0 = no limit; null removes the setting)
  GET  /admin/llm-assignments   the tiers of the model profiles file and who has which (rag_core/llm_assignments.py)
  POST /admin/llm-assignments   assign a tier to everyone, a tenant, a role or a caller, with a reason (installation administrator)

A tenant administrator sees their own tenants' calls and use, and sets limits for their own tenants. The downtime, the global limit
and the assignments are the installation's: only the installation administrator.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

import config
from rag_core import availability, llm_assignments, llm_quota, llm_usage, runtime_settings
from webapi import admin_common
from webapi.admin_auth import require_admin_auth, require_global, require_scope

router = APIRouter()
_GROUPS = ("tenant_id", "feature", "profile", "model", "key_fingerprint", "location")


@router.get("/admin/usage")
def usage(request: Request, days: int = 7, by: str = "tenant_id", _admin_auth: None = Depends(require_admin_auth)):
    if by not in _GROUPS:
        raise HTTPException(status_code=400, detail=f"'by' is one of: {', '.join(_GROUPS)}")
    scope = admin_common.request_scope(request)
    days = max(1, min(days, 400))
    return {"days": days, "by": by, "rows": llm_usage.summarize(days, by=by, tenants=admin_common.allowed_tenants(scope))}


@router.get("/admin/usage/daily")
def usage_daily(request: Request, days: int = 30, _admin_auth: None = Depends(require_admin_auth)):
    scope = admin_common.request_scope(request)
    days = max(1, min(days, 400))
    return {"days": days, "rows": llm_usage.daily(days, tenants=admin_common.allowed_tenants(scope))}


@router.get("/admin/usage/downtime")
def usage_downtime(request: Request, days: int = 30, _admin_auth: None = Depends(require_admin_auth)):
    require_global(admin_common.request_scope(request))
    report = llm_usage.downtime_report(max(1, min(days, 400)))
    report["business_hours"] = {"utc_offset_hours": config.BUSINESS_HOURS_UTC_OFFSET, "start": config.BUSINESS_HOURS_START,
                                "end": config.BUSINESS_HOURS_END, "weekdays_only": True, "holidays_known": False}
    return report


# --- limits and model assignments (S6) --------------------------------------------------------------------------------------------------------


class LimitRequest(BaseModel):
    name: str = Field(..., max_length=40)
    value: Optional[float] = None  # None removes the setting; 0 = no limit
    reason: str = Field(..., min_length=1, max_length=300)
    tenant_id: Optional[str] = Field(None, max_length=64)


class AssignmentRequest(BaseModel):
    kind: str = Field(..., max_length=10)
    id: Optional[str] = Field(None, max_length=64)
    tier: Optional[str] = Field(None, max_length=40)  # None removes the assignment
    reason: str = Field(..., min_length=1, max_length=300)


@router.get("/admin/limits")
def limits(request: Request, tenant_id: Optional[str] = None, _admin_auth: None = Depends(require_admin_auth)):
    """The daily limits in force and today's use against them (the tenants an administrator may see)."""
    scope = admin_common.request_scope(request)
    if tenant_id:
        require_scope(scope, tenant_id)
    tenants: Optional[List[str]] = None if scope is None else sorted(scope)
    return llm_quota.snapshot(tenant_id, tenants=tenants)


@router.post("/admin/limits")
def set_limit(req: LimitRequest, request: Request, _admin_auth: None = Depends(require_admin_auth)):
    scope = admin_common.request_scope(request)
    if req.tenant_id:
        require_scope(scope, req.tenant_id)
    else:
        require_global(scope)
    try:
        change = llm_quota.set_limit(req.name, req.value, by=admin_common.admin_label(scope), reason=req.reason, tenant_id=req.tenant_id)
    except runtime_settings.SettingsError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    admin_common.audit_admin(scope, "llm_limit_set", limit=req.name, value=req.value, tenant_id=req.tenant_id, rev=change["rev"])
    llm_quota.reset()  # the next check reads the files again
    tenants: Optional[List[str]] = None if scope is None else sorted(scope)
    return {"change": change, **llm_quota.snapshot(req.tenant_id, tenants=tenants)}


@router.get("/admin/llm-assignments")
def assignments(_admin_auth: None = Depends(require_admin_auth)):
    """The tiers of the model profiles file and who has which."""
    return llm_assignments.list_assignments()


@router.post("/admin/llm-assignments")
def set_assignment(req: AssignmentRequest, request: Request, _admin_auth: None = Depends(require_admin_auth)):
    scope = admin_common.request_scope(request)
    require_global(scope)  # which model answers is a decision about cost and quality for the installation
    try:
        change = llm_assignments.set_assignment(req.kind, req.id, req.tier, by=admin_common.admin_label(scope), reason=req.reason)
    except runtime_settings.SettingsError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    admin_common.audit_admin(scope, "llm_assignment_set", kind=req.kind, target=req.id, tier=req.tier, rev=change["rev"])
    return {"change": change, **llm_assignments.list_assignments()}


# --- availability measured from outside (S7) ---------------------------------------------------------------------------------------------------


@router.get("/admin/availability")
def availability_report(request: Request, days: int = 30, _admin_auth: None = Depends(require_admin_auth)):
    """The availability in business hours from the log of the external probe (rag_core/availability.py): the service cannot measure its
    own downtime. Installation administrator only."""
    require_global(admin_common.request_scope(request))
    path = str(getattr(config, "PROBE_LOG_FILE", "") or "").strip()
    if not path:
        return {"configured": False, "hint": "PROBE_LOG_FILE is not set: run scripts/external_probe.py on another host and point this setting at its log"}
    days = max(1, min(days, 400))
    result = availability.report_for_days(
        path, days, max_gap_seconds=float(getattr(config, "PROBE_MAX_GAP_SECONDS", 300.0)),
        utc_offset_hours=float(config.BUSINESS_HOURS_UTC_OFFSET), first_hour=int(config.BUSINESS_HOURS_START),
        last_hour=int(config.BUSINESS_HOURS_END), target_percent=float(getattr(config, "AVAILABILITY_TARGET_PERCENT", 99.0)),
    )
    result["configured"] = True
    result["log_file_found"] = Path(path).exists()
    return result
