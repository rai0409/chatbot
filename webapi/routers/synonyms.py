"""Synonym candidate review (N8): the admin page and the decision / apply routes (moved from main.py, S1)."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from webapi import admin_common
from webapi import synonym_review
from webapi.admin_auth import require_admin_auth, require_global

router = APIRouter()

_STATIC_DIR = admin_common.STATIC_DIR
_request_scope = admin_common.request_scope
_audit_admin = admin_common.audit_admin
_admin_page_html = admin_common.admin_page_html


class SynonymDecisionRequest(BaseModel):
    candidate_id: str = Field(..., max_length=64)
    status: str


class SynonymApplyRequest(BaseModel):
    profile_id: str = Field(..., max_length=64)


@router.get("/admin/synonyms", response_class=HTMLResponse)
def synonym_review_page(_admin_auth: None = Depends(require_admin_auth)):
    path = _STATIC_DIR / "synonym_review.html"
    try:
        return HTMLResponse(_admin_page_html(path))
    except Exception:
        logging.exception("synonym review page unavailable")
        raise HTTPException(status_code=500, detail="synonym review page unavailable")


@router.get("/admin/synonyms/candidates")
def synonym_candidates(request: Request = None, status: Optional[str] = None, limit: int = 200,
                       _admin_auth: None = Depends(require_admin_auth)):
    # Candidates come from every tenant's corpus: installation administrator only.
    require_global(_request_scope(request))
    if status and status not in synonym_review.STATUSES:
        raise HTTPException(status_code=400, detail="invalid status")
    return synonym_review.list_candidates(status=status, limit=limit)


@router.post("/admin/synonyms/decision")
def synonym_decision(req: SynonymDecisionRequest, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    scope = _request_scope(request)
    require_global(scope)
    _audit_admin(scope, "synonym_decision", candidate_id=req.candidate_id, status=req.status)
    try:
        return synonym_review.decide(req.candidate_id, req.status, decided_by="admin")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except KeyError:
        raise HTTPException(status_code=404, detail="candidate not found")


@router.post("/admin/synonyms/apply")
def synonym_apply(req: SynonymApplyRequest, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    scope = _request_scope(request)
    require_global(scope)
    _audit_admin(scope, "synonym_apply", profile_id=req.profile_id)
    try:
        return synonym_review.apply_to_profile(req.profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
