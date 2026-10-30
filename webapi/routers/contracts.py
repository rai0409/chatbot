"""Contracts (7-3): the admin page and the contract routes (moved from main.py, S1)."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from rag_core import contract_notify, contracts
from rag_core import integrations, source_registry
from rag_core.connectors import outlook as outlook_connector
from webapi import admin_common
from webapi.admin_auth import require_admin_auth, require_scope

router = APIRouter()

_STATIC_DIR = admin_common.STATIC_DIR
_request_scope = admin_common.request_scope
_audit_admin = admin_common.audit_admin
_admin_label = admin_common.admin_label
_admin_page_html = admin_common.admin_page_html


class ContractImportRequest(BaseModel):
    tenant_id: str = Field("default", max_length=64)
    contract_id: str = Field(..., max_length=64)
    source_doc: str = Field("", max_length=512)
    extracted: Dict[str, Any]


class ContractFieldRequest(BaseModel):
    tenant_id: str = Field("default", max_length=64)
    name: str = Field(..., max_length=40)
    value: Optional[Any] = None
    note: Optional[str] = Field(None, max_length=500)


class ContractNotifyRequest(BaseModel):
    tenant_id: str = Field("default", max_length=64)
    dry_run: bool = True


class ContractDraftRequest(BaseModel):
    tenant_id: str = Field("default", max_length=64)
    kind: str = Field(..., max_length=40)
    mailbox: str = Field(..., max_length=320)


def _contract_http_error(exc: Exception) -> HTTPException:
    text = str(exc)
    if "not found" in text:
        return HTTPException(status_code=404, detail=text)
    if "already exists" in text or "changed by someone else" in text:
        return HTTPException(status_code=409, detail=text)
    return HTTPException(status_code=400, detail=text)


def _contract_scope(request, tenant_id: str):
    scope = _request_scope(request)
    require_scope(scope, tenant_id)
    return scope


def _contract_summary(contract: Dict[str, Any]) -> Dict[str, Any]:
    items = contracts.deadlines(contract)
    ahead = sorted((d for d in items if d["days_left"] >= 0), key=lambda d: d["date"])
    nearest = ahead[0] if ahead else (max(items, key=lambda d: d["date"]) if items else None)
    fields = contract["fields"]
    return {"contract_id": contract["contract_id"], "counterparty": fields["counterparty"]["value"], "title": fields["title"]["value"],
            "status": contracts.status_of(contract), "source_doc": contract["source_doc"], "nearest_deadline": nearest,
            "unconfirmed": sum(1 for f in fields.values() if f["value"] is not None and f["state"] == "extracted")}


@router.get("/admin/contracts", response_class=HTMLResponse)
def contracts_page(_admin_auth: None = Depends(require_admin_auth)):
    path = _STATIC_DIR / "contracts.html"
    try:
        return HTMLResponse(_admin_page_html(path))
    except Exception:
        logging.exception("contracts page unavailable")
        raise HTTPException(status_code=500, detail="contracts page unavailable")


@router.get("/admin/contracts/list")
def contracts_list(tenant_id: str = Query("default", max_length=64), request: Request = None,
                   _admin_auth: None = Depends(require_admin_auth)):
    _contract_scope(request, tenant_id)
    try:
        return {"contracts": [_contract_summary(c) for c in contracts.list_contracts(tenant_id)]}
    except contracts.ContractError as exc:
        raise _contract_http_error(exc)


@router.get("/admin/contracts/upcoming")
def contracts_upcoming(tenant_id: str = Query("default", max_length=64), within_days: int = Query(90, ge=0, le=730),
                       request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    _contract_scope(request, tenant_id)
    try:
        return {"deadlines": contracts.upcoming(tenant_id, within_days=within_days)}
    except contracts.ContractError as exc:
        raise _contract_http_error(exc)


@router.post("/admin/contracts/import")
def contracts_import(req: ContractImportRequest, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    scope = _contract_scope(request, req.tenant_id)
    try:
        out = contracts.import_extracted(req.tenant_id, req.contract_id, req.extracted, source_doc=req.source_doc,
                                         created_by=_admin_label(scope))
    except contracts.ContractError as exc:
        raise _contract_http_error(exc)
    _audit_admin(scope, "contract_imported", tenant_id=req.tenant_id, contract_id=req.contract_id,
                 rejected=sorted(out["rejected"]), kept=out["kept"])
    return {"contract": _contract_summary(out["contract"]), "rejected": out["rejected"], "kept": out["kept"]}


@router.post("/admin/contracts/notify")
def contracts_notify(req: ContractNotifyRequest, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    scope = _contract_scope(request, req.tenant_id)
    try:
        out = contract_notify.run(req.tenant_id, dry_run=req.dry_run)
    except contracts.ContractError as exc:
        raise _contract_http_error(exc)
    _audit_admin(scope, "contract_notify_run", tenant_id=req.tenant_id, dry_run=req.dry_run, due=out["due"], sent=out["sent"],
                 failed=out["failed"])
    return out


@router.get("/admin/contracts/{contract_id}")
def contracts_detail(contract_id: str, tenant_id: str = Query("default", max_length=64), request: Request = None,
                     _admin_auth: None = Depends(require_admin_auth)):
    _contract_scope(request, tenant_id)
    try:
        contract = contracts.get(tenant_id, contract_id)
    except contracts.ContractError as exc:
        raise _contract_http_error(exc)
    return {**contract, "history": contract["history"][-50:], "status": contracts.status_of(contract),
            "deadlines": contracts.deadlines(contract), "required": list(contracts.REQUIRED)}


@router.post("/admin/contracts/{contract_id}/field")
def contracts_set_field(contract_id: str, req: ContractFieldRequest, request: Request = None,
                        _admin_auth: None = Depends(require_admin_auth)):
    scope = _contract_scope(request, req.tenant_id)
    try:
        contract = contracts.set_field(req.tenant_id, contract_id, req.name, req.value, by=_admin_label(scope), note=req.note or "")
    except contracts.ContractError as exc:
        raise _contract_http_error(exc)
    _audit_admin(scope, "contract_field_set", tenant_id=req.tenant_id, contract_id=contract_id, field=req.name)
    return _contract_summary(contract)


@router.post("/admin/contracts/{contract_id}/confirm")
def contracts_confirm_field(contract_id: str, req: ContractFieldRequest, request: Request = None,
                            _admin_auth: None = Depends(require_admin_auth)):
    scope = _contract_scope(request, req.tenant_id)
    try:
        contract = contracts.confirm_field(req.tenant_id, contract_id, req.name, by=_admin_label(scope))
    except contracts.ContractError as exc:
        raise _contract_http_error(exc)
    _audit_admin(scope, "contract_field_confirmed", tenant_id=req.tenant_id, contract_id=contract_id, field=req.name)
    return _contract_summary(contract)


@router.post("/admin/contracts/{contract_id}/draft")
def contracts_draft(contract_id: str, req: ContractDraftRequest, request: Request = None,
                    _admin_auth: None = Depends(require_admin_auth)):
    scope = _contract_scope(request, req.tenant_id)
    try:
        integrations.require("outlook_draft", req.tenant_id)  # environment layer, console switch, tenant switch
    except integrations.IntegrationDisabled as exc:
        raise HTTPException(status_code=409, detail=f"integration_off:{exc.status.reason}")
    try:
        client = outlook_connector.default_client()
        out = outlook_connector.draft_for_deadline(req.tenant_id, contract_id, req.kind, client=client, mailbox=req.mailbox,
                                                   by=_admin_label(scope))
    except contracts.ContractError as exc:
        raise _contract_http_error(exc)
    except source_registry.SourceError:
        raise HTTPException(status_code=409, detail="mail access is not configured (OUTLOOK_ACCESS_TOKEN or the client credentials)")
    except Exception:  # noqa: BLE001 - a Graph failure is reported without its details
        logging.exception("contract draft failed contract_id=%s", contract_id)
        raise HTTPException(status_code=502, detail="the draft could not be created")
    _audit_admin(scope, "contract_draft_created", tenant_id=req.tenant_id, contract_id=contract_id, kind=req.kind)
    return out
