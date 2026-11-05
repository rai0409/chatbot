"""Extraction review (6-3): the admin page and the review routes (moved from main.py, S1)."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from rag_core import extraction_review, extraction_store
from webapi import admin_common
from webapi.admin_auth import require_admin_auth, require_scope

router = APIRouter()

_STATIC_DIR = admin_common.STATIC_DIR
_request_scope = admin_common.request_scope
_audit_admin = admin_common.audit_admin
_admin_label = admin_common.admin_label
_admin_page_html = admin_common.admin_page_html


class ExtractionLineRequest(BaseModel):
    tenant_id: str = Field("default", max_length=64)
    source_doc: str = Field(..., max_length=512)
    page: int = Field(..., ge=1, le=100000)
    line_id: str = Field(..., max_length=32)
    text: Optional[str] = Field(None, max_length=2000)
    note: Optional[str] = Field(None, max_length=500)
    # The name of the person at the keyboard (kept in the record). With a single sign-on session their
    # identifier is added, which is what makes "a different person" checkable.
    actor: Optional[str] = Field(None, max_length=64)


class ExtractionDocRequest(BaseModel):
    tenant_id: str = Field("default", max_length=64)
    source_doc: str = Field(..., max_length=512)


def _extraction_http_error(exc: Exception) -> HTTPException:
    # By type, not by message wording: a reworded message cannot change the status.
    if isinstance(exc, extraction_store.ExtractionNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def _extraction_scope(request, tenant_id: str):
    scope = _request_scope(request)
    require_scope(scope, tenant_id)
    return scope


@router.get("/admin/extraction", response_class=HTMLResponse)
def extraction_page(_admin_auth: None = Depends(require_admin_auth)):
    path = _STATIC_DIR / "extraction_review.html"
    try:
        return HTMLResponse(_admin_page_html(path))
    except Exception:
        logging.exception("extraction review page unavailable")
        raise HTTPException(status_code=500, detail="extraction review page unavailable")


@router.get("/admin/extraction/docs")
def extraction_docs(tenant_id: str = Query("default", max_length=64), request: Request = None,
                    _admin_auth: None = Depends(require_admin_auth)):
    _extraction_scope(request, tenant_id)
    try:
        return {"documents": extraction_review.documents(tenant_id)}
    except extraction_store.ExtractionError as exc:
        raise _extraction_http_error(exc)


@router.get("/admin/extraction/items")
def extraction_items(
    tenant_id: str = Query("default", max_length=64), max_confidence: Optional[float] = Query(None, ge=0, le=100),
    include_reviewed: bool = False, source_doc: Optional[str] = Query(None, max_length=512),
    limit: int = Query(100, ge=1, le=500), request: Request = None, _admin_auth: None = Depends(require_admin_auth),
):
    _extraction_scope(request, tenant_id)
    try:
        return {"items": extraction_review.review_items(tenant_id, max_confidence=max_confidence,
                                                        include_reviewed=include_reviewed, source_doc=source_doc, limit=limit)}
    except extraction_store.ExtractionError as exc:
        raise _extraction_http_error(exc)


@router.get("/admin/extraction/page")
def extraction_page_data(
    source_doc: str = Query(..., max_length=512), page: int = Query(..., ge=1, le=100000), tenant_id: str = Query("default", max_length=64),
    request: Request = None, _admin_auth: None = Depends(require_admin_auth),
):
    _extraction_scope(request, tenant_id)
    try:
        record = extraction_store.load_record(tenant_id, source_doc)
        entry_page = None if record is None else next((p for p in record["pages"] if p["page"] == page), None)
        if entry_page is None:
            raise extraction_store.ExtractionNotFound("no extraction record for this page")
        entries = extraction_review.line_entries(tenant_id, source_doc)
    except extraction_store.ExtractionError as exc:
        raise _extraction_http_error(exc)
    def _line_payload(line, occurrence):
        entry = entries.get((page, line["text"], occurrence))
        return {**line, "state": entry["state"] if entry else "open",
                "approval": extraction_review.approval_state(entry),
                "corrected_by": entry.get("by") if entry and entry["state"] == "corrected" else None,
                "approved_by": entry.get("approved_by") if entry else None}

    return {"source_doc": source_doc, "page": page, "width_px": entry_page["width_px"], "height_px": entry_page["height_px"],
            "confidence": entry_page["confidence"], "preview_available": bool(record.get("file_path")),
            "threshold": extraction_store.review_max_confidence(),
            "require_approval": extraction_review.require_approval(),
            "lines": [_line_payload(line, occurrence) for line, occurrence in extraction_review._with_occurrence(entry_page)]}


@router.get("/admin/extraction/page-image")
def extraction_page_image(
    source_doc: str = Query(..., max_length=512), page: int = Query(..., ge=1, le=100000), tenant_id: str = Query("default", max_length=64),
    request: Request = None, _admin_auth: None = Depends(require_admin_auth),
):
    _extraction_scope(request, tenant_id)
    try:
        record = extraction_store.load_record(tenant_id, source_doc)
        if record is None:
            raise extraction_store.ExtractionNotFound("no extraction record for this document")
        png = extraction_store.render_page_png(record, page)
    except extraction_store.ExtractionError as exc:
        raise _extraction_http_error(exc)
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "private, max-age=60"})


def _extraction_decision(req: ExtractionLineRequest, request, action: str):
    scope = _extraction_scope(request, req.tenant_id)
    # A typed name (and the single sign-on identifier, when there is a session); else the admin credential's reach.
    who = admin_common.actor_identity(request, req.actor) if req.actor else _admin_label(scope)
    if action == "approve" and not req.actor:
        raise HTTPException(status_code=400, detail="actor (the name of the approver) is required")
    try:
        if action == "correct":
            result = extraction_review.submit_correction(req.tenant_id, req.source_doc, req.page, req.line_id, req.text or "",
                                                         by=who, note=req.note or "")
        elif action == "approve":
            result = extraction_review.approve_correction(req.tenant_id, req.source_doc, req.page, req.line_id, by=who,
                                                          note=req.note or "")
        elif action == "confirm":
            result = extraction_review.confirm_line(req.tenant_id, req.source_doc, req.page, req.line_id, by=who, note=req.note or "")
        else:
            result = {"reverted": extraction_review.revert(req.tenant_id, req.source_doc, req.page, req.line_id)}
    except extraction_store.ExtractionError as exc:
        raise _extraction_http_error(exc)
    _audit_admin(scope, f"extraction_{action}d" if action.endswith("e") else f"extraction_{action}ed",  # corrected / confirmed / reverted / approved
                 tenant_id=req.tenant_id, source_doc=req.source_doc, page=req.page, line_id=req.line_id)
    return result


@router.post("/admin/extraction/correct")
def extraction_correct(req: ExtractionLineRequest, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    return _extraction_decision(req, request, "correct")


@router.post("/admin/extraction/approve")
def extraction_approve(req: ExtractionLineRequest, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    return _extraction_decision(req, request, "approve")


@router.post("/admin/extraction/confirm")
def extraction_confirm(req: ExtractionLineRequest, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    return _extraction_decision(req, request, "confirm")


@router.post("/admin/extraction/revert")
def extraction_revert(req: ExtractionLineRequest, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    return _extraction_decision(req, request, "revert")


@router.post("/admin/extraction/reingest")
def extraction_reingest(req: ExtractionDocRequest, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    scope = _extraction_scope(request, req.tenant_id)
    try:
        sources = extraction_review.request_reingest(req.tenant_id, req.source_doc)
    except extraction_store.ExtractionError as exc:
        raise _extraction_http_error(exc)
    _audit_admin(scope, "extraction_reingest_requested", tenant_id=req.tenant_id, source_doc=req.source_doc, sources=sources)
    return {"sources": sources, "note": "the document is ingested again by the next apply of those sources"
            if sources else "no registered source holds this document: upload it again"}
