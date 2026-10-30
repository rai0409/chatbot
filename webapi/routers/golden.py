"""The golden questions of a tenant (S4): what a good answer contains, kept and edited in the console.

  GET    /admin/golden/{tenant}                 the cases (disabled ones too)
  POST   /admin/golden/{tenant}                 a new case
  GET    /admin/golden/{tenant}/export          the enabled cases as an evaluation takes them
  POST   /admin/golden/{tenant}/from-record     a case made from a record a person judged
  GET    /admin/golden/{tenant}/{case_id}       one case
  PUT    /admin/golden/{tenant}/{case_id}       change it (expected_updated_at guards against a change made meanwhile)
  DELETE /admin/golden/{tenant}/{case_id}       move it to the tenant's deleted cases

A tenant administrator works on their own tenants' cases only.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from rag_core import golden_store, records
from webapi import admin_common
from webapi.admin_auth import require_admin_auth, require_scope

router = APIRouter()


class CaseBody(BaseModel):
    case_id: Optional[str] = Field(None, max_length=64)
    question: Optional[str] = Field(None, max_length=1000)
    expected_all: Optional[List[str]] = Field(None, max_length=30)
    expected_any: Optional[List[str]] = Field(None, max_length=30)
    forbidden_any: Optional[List[str]] = Field(None, max_length=30)
    expected_question_type: Optional[str] = Field(None, max_length=40)
    group: Optional[str] = Field(None, max_length=80)
    origin_change: Optional[str] = Field(None, max_length=80)
    reference: Optional[str] = Field(None, max_length=4000)
    tags: Optional[List[str]] = Field(None, max_length=20)
    expected_citation: Optional[bool] = None
    enabled: Optional[bool] = None
    priority: Optional[int] = None
    expected_updated_at: Optional[str] = Field(None, max_length=60)

    def fields(self) -> Dict[str, Any]:
        data = self.model_dump(exclude_none=True)
        data.pop("expected_updated_at", None)
        return data


class FromRecordBody(BaseModel):
    record_id: str = Field(..., max_length=64)
    case_id: Optional[str] = Field(None, max_length=64)


def _guard(request: Request, tenant: str):
    scope = admin_common.request_scope(request)
    require_scope(scope, tenant)
    return scope


def _wrap(call):
    try:
        return call()
    except golden_store.GoldenError as exc:
        text = str(exc)
        code = 404 if "not found" in text else 409 if ("already exists" in text or "someone else" in text) else 400
        raise HTTPException(status_code=code, detail=text)


@router.get("/admin/golden/{tenant}")
def list_cases(tenant: str, request: Request, _admin_auth: None = Depends(require_admin_auth)):
    _guard(request, tenant)
    cases = _wrap(lambda: golden_store.list_entries(tenant))
    return {"tenant_id": tenant, "total": len(cases), "enabled": sum(1 for c in cases if c.get("enabled", True)), "cases": cases}


@router.post("/admin/golden/{tenant}")
def create_case(tenant: str, body: CaseBody, request: Request, _admin_auth: None = Depends(require_admin_auth)):
    scope = _guard(request, tenant)
    case = _wrap(lambda: golden_store.create(tenant, body.fields(), by=admin_common.admin_label(scope)))
    admin_common.audit_admin(scope, "golden_created", tenant_id=tenant, case_id=case["case_id"])
    return case


@router.get("/admin/golden/{tenant}/export")
def export_cases(tenant: str, request: Request, _admin_auth: None = Depends(require_admin_auth)):
    _guard(request, tenant)
    return {"tenant_id": tenant, "golden_qa": _wrap(lambda: golden_store.export_cases(tenant))}


@router.post("/admin/golden/{tenant}/from-record")
def from_record(tenant: str, body: FromRecordBody, request: Request, _admin_auth: None = Depends(require_admin_auth)):
    scope = _guard(request, tenant)
    record = records.get_record(body.record_id)
    if record is None or record.get("tenant_id") != tenant:
        raise HTTPException(status_code=404, detail="record not found")
    case = _wrap(lambda: golden_store.promote_record(tenant, record, by=admin_common.admin_label(scope), case_id=body.case_id))
    admin_common.audit_admin(scope, "golden_from_record", tenant_id=tenant, case_id=case["case_id"], record_id=body.record_id)
    return case


@router.get("/admin/golden/{tenant}/{case_id}")
def get_case(tenant: str, case_id: str, request: Request, _admin_auth: None = Depends(require_admin_auth)):
    _guard(request, tenant)
    return _wrap(lambda: golden_store.get(tenant, case_id))


@router.put("/admin/golden/{tenant}/{case_id}")
def update_case(tenant: str, case_id: str, body: CaseBody, request: Request, _admin_auth: None = Depends(require_admin_auth)):
    scope = _guard(request, tenant)
    case = _wrap(lambda: golden_store.update(tenant, case_id, body.fields(), by=admin_common.admin_label(scope),
                                             expected_updated_at=body.expected_updated_at))
    admin_common.audit_admin(scope, "golden_updated", tenant_id=tenant, case_id=case_id)
    return case


@router.delete("/admin/golden/{tenant}/{case_id}")
def delete_case(tenant: str, case_id: str, request: Request, _admin_auth: None = Depends(require_admin_auth)):
    scope = _guard(request, tenant)
    _wrap(lambda: golden_store.delete(tenant, case_id, by=admin_common.admin_label(scope)))
    admin_common.audit_admin(scope, "golden_deleted", tenant_id=tenant, case_id=case_id)
    return {"ok": True}
