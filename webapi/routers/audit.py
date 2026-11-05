"""Reading the audit log (S4).

  GET /admin/audit          events newest first: kind = admin (changes, settings) | chat | feedback; filters; paging
  GET /admin/audit/verify   recompute the hash chain of one kind (installation administrator only)

A tenant administrator sees the events of their own tenants only (an event that belongs to no tenant is hidden from them).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from rag_core import audit_view
from webapi import admin_common
from webapi.admin_auth import require_admin_auth, require_global

router = APIRouter()


def _kind(kind: str) -> str:
    if kind not in audit_view.FILES:
        raise HTTPException(status_code=400, detail=f"'kind' is one of: {', '.join(audit_view.FILES)}")
    return kind


@router.get("/admin/audit")
def audit_events(
    request: Request, kind: str = "admin", limit: int = 100, offset: int = 0, text: Optional[str] = None,
    action: Optional[str] = None, since: Optional[str] = None, _admin_auth: None = Depends(require_admin_auth),
):
    scope = admin_common.request_scope(request)
    return audit_view.read_events(_kind(kind), limit=limit, offset=offset, text=text, action=action,
                                  tenants=admin_common.allowed_tenants(scope), since=since)


@router.get("/admin/audit/verify")
def audit_verify(request: Request, kind: str = "admin", _admin_auth: None = Depends(require_admin_auth)):
    require_global(admin_common.request_scope(request))
    return audit_view.verify(_kind(kind))
