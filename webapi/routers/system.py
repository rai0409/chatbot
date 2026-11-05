"""System status and the switches of the external integrations (S1).

  GET  /admin/system/status    every integration with its state and the layer that said no, plus the settings revision
  POST /admin/system/switches  turn an integration on or off (globally, or for one tenant), with a reason
  GET  /admin/system/changes   the latest changes of the switches (the whole chain is in the audit log)
  GET  /admin/system/approvals              requests to turn an integration on, waiting for a second person (or decided)
  POST /admin/system/approvals/{id}/{approve|reject|cancel}   decide a request (rag_core/approvals.py)

A change needs a reason and is written to the audit log. The environment layer (EXTERNAL_INTEGRATIONS) cannot be widened
from here, and a tenant administrator can only restrict their own tenants; the installation-wide switch needs the
installation administrator. Turning an integration OFF (or taking a tenant out) takes effect at once. Turning one ON is a request
that a different person approves (INTEGRATION_APPROVALS_REQUIRED=2, the default): the POST answers 202 with the pending request.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import config
from rag_core import approvals, integrations, llm, llm_gateway, llm_profiles, runtime_settings
from rag_core.retrieval import normalize_tenant_id
from webapi import admin_common, breaker_store, oidc_auth
from webapi.admin_auth import require_admin_auth, require_global, require_scope
from webapi.api_auth import ApiAuthContext, enforce_tenant_authorization
from webapi.rate_limit import require_api_auth_rate_limited

router = APIRouter()

# BREAKER_BACKEND=redis: the circuit breakers of the LLM endpoints are shared between the workers (webapi/breaker_store.py).
breaker_store.install()


def _oidc_probe(_tenant_id: Optional[str]) -> integrations.Status:
    label = integrations._LABELS["oidc"]
    if oidc_auth.oidc_enabled():
        return integrations.Status("oidc", label, integrations.OK, True, True, "", "")
    return integrations.Status("oidc", label, integrations.DISABLED, False, True, "environment", "oidc_not_enabled")


integrations.register_probe("oidc", _oidc_probe)


class SwitchRequest(BaseModel):
    name: str = Field(..., max_length=40)
    enabled: bool
    reason: str = Field(..., min_length=1, max_length=300)
    tenant_id: Optional[str] = Field(None, max_length=64)
    expected_rev: Optional[int] = None
    actor: Optional[str] = Field(None, max_length=64)  # who asks: required when turning on needs a second person's approval


class DecisionRequest(BaseModel):
    actor: str = Field(..., min_length=1, max_length=64)
    reason: str = Field("", max_length=300)


def status_payload(tenant_id: Optional[str] = None) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = [s.to_dict() for s in integrations.all_status(tenant_id)]
    return {
        "tenant_id": tenant_id,
        "revision": runtime_settings.revision(),
        "environment": {"external_integrations": list(getattr(config, "EXTERNAL_INTEGRATIONS", []) or [])},
        "integrations": items,
        "llm": {"profiles": llm_gateway.status(), "profiles_file_problems": llm_profiles.validate()},
        "warnings": integrations.startup_warnings(),
        "approvals": {"required": approvals.required(), "pending": approvals.pending_count(),
                      "require_sso": bool(getattr(config, "APPROVAL_REQUIRE_SSO", False))},
    }


def public_status(tenant_id: Optional[str] = None) -> Dict[str, Any]:
    """What a user's screen needs, and nothing more: is the service normal, or is it answering without the LLM (and why,
    as a code). No host, model or profile name, no count."""
    llm_now = integrations.status("llm", tenant_id)
    generating = llm.generation_mode(tenant_id or "default") == "llm"
    degraded = generating and llm_now.state == integrations.FAILING
    return {
        "service": "degraded" if degraded else "normal",
        "answers": "extractive" if (degraded or not generating) else "generated",
        "reason": "llm_unavailable" if degraded else "",
    }


@router.get("/system/status")
def system_status_public(tenant_id: Optional[str] = None, api_auth: ApiAuthContext = Depends(require_api_auth_rate_limited)):
    tenant = normalize_tenant_id(tenant_id)
    enforce_tenant_authorization(api_auth, tenant)
    return public_status(tenant)


@router.get("/admin/system/status")
def system_status(request: Request, tenant_id: Optional[str] = None, _admin_auth: None = Depends(require_admin_auth)):
    scope = admin_common.request_scope(request)
    if tenant_id:
        require_scope(scope, tenant_id)
    return status_payload(tenant_id)


@router.post("/admin/system/switches")
def system_switch(req: SwitchRequest, request: Request, _admin_auth: None = Depends(require_admin_auth)):
    scope = admin_common.request_scope(request)
    if req.tenant_id:
        require_scope(scope, req.tenant_id)
    else:
        require_global(scope)
    if req.enabled and req.name in integrations.GATED and approvals.required() >= 2:
        return _ask_for_approval(req, request, scope)  # turning on widens what may leave: a second person decides
    try:
        record = integrations.set_switch(
            req.name, req.enabled, by=admin_common.admin_label(scope), reason=req.reason, tenant_id=req.tenant_id
        ) if req.expected_rev is None else _guarded(req, scope)
    except runtime_settings.Conflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except runtime_settings.SettingsError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    admin_common.audit_admin(scope, "integration_switch", integration=req.name, enabled=req.enabled,
                             tenant_id=req.tenant_id, rev=record["rev"])
    return {"change": record, **status_payload(req.tenant_id)}


def _already_on(req: SwitchRequest) -> bool:
    return (not integrations.tenant_off(req.name, req.tenant_id)) if req.tenant_id else integrations.switch_on(req.name)


def _ask_for_approval(req: SwitchRequest, request: Request, scope):
    if _already_on(req):
        return {"unchanged": True, **status_payload(req.tenant_id)}
    who = admin_common.actor_identity(request, req.actor)  # 400 without a name
    params = {"name": req.name, "tenant_id": req.tenant_id}
    try:
        item = approvals.request_change(approvals.KIND_ENABLE, params, reason=req.reason, requested_by=who)
    except approvals.ApprovalError as exc:
        raise HTTPException(status_code=409 if "already waiting" in str(exc) else 400, detail=str(exc))
    admin_common.audit_admin(scope, "integration_enable_requested", integration=req.name, tenant_id=req.tenant_id, approval_id=item["id"])
    return JSONResponse(status_code=202, content={"pending": item, **status_payload(req.tenant_id)})


@router.get("/admin/system/approvals")
def list_approvals(request: Request, status: Optional[str] = None, _admin_auth: None = Depends(require_admin_auth)):
    if status is not None and status not in approvals.STATUSES:
        raise HTTPException(status_code=400, detail=f"'status' is one of: {', '.join(approvals.STATUSES)}")
    scope = admin_common.request_scope(request)
    items = approvals.list_items(status)
    if scope is not None:  # a tenant administrator sees the requests about their own tenants only
        items = [i for i in items if i["params"].get("tenant_id") in scope]
    return {"required": approvals.required(), "require_sso": bool(getattr(config, "APPROVAL_REQUIRE_SSO", False)), "approvals": items}


@router.post("/admin/system/approvals/{approval_id}/{decision}")
def decide_approval(approval_id: str, decision: str, body: DecisionRequest, request: Request, _admin_auth: None = Depends(require_admin_auth)):
    scope = admin_common.request_scope(request)
    if decision not in approvals.DECISIONS:
        raise HTTPException(status_code=404, detail=f"a decision is one of: {', '.join(approvals.DECISIONS)}")
    try:
        item = approvals.get(approval_id)
    except approvals.ApprovalError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    tenant = item["params"].get("tenant_id")
    if tenant:
        require_scope(scope, tenant)  # a request about a tenant is decided by someone who reaches that tenant
    else:
        require_global(scope)  # an installation-wide request is decided by the installation administrator
    who = admin_common.actor_identity(request, body.actor)
    try:
        done = approvals.decide(approval_id, decision, decided_by=who, reason=body.reason)
    except (approvals.ApprovalError, runtime_settings.SettingsError) as exc:
        raise HTTPException(status_code=400 if "not found" not in str(exc) else 404, detail=str(exc))
    admin_common.audit_admin(scope, "integration_enable_" + done["status"], integration=item["params"]["name"], tenant_id=tenant,
                             approval_id=approval_id, actor=who["name"], verified=who["verified"])
    return {"approval": done, **status_payload(tenant)}


def _guarded(req: SwitchRequest, scope) -> Dict[str, Any]:
    # With expected_rev, a change made by someone else in the meantime is a conflict (409), not a silent overwrite.
    if runtime_settings.revision() != int(req.expected_rev or 0):
        raise runtime_settings.Conflict("the settings were changed by someone else; reload and try again")
    return integrations.set_switch(
        req.name, req.enabled, by=admin_common.admin_label(scope), reason=req.reason, tenant_id=req.tenant_id
    )


@router.get("/admin/system/changes")
def system_changes(request: Request, limit: int = 50, _admin_auth: None = Depends(require_admin_auth)):
    scope = admin_common.request_scope(request)
    rows = runtime_settings.history(max(1, min(int(limit), 200)))
    if scope is not None:  # a tenant administrator sees the changes of their own tenants only
        rows = [r for r in rows if r.get("tenant_id") in scope]
    return {"revision": runtime_settings.revision(), "changes": rows}
