"""Admin browser login / logout, the admin dashboard page, the metrics summary and the review queue
(moved from main.py, S7). The review action route stays in main.py."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

import config
from rag_core.audit_log import write_failure_count
from rag_core.review_actions import latest_review_statuses
from webapi import admin_common
from webapi import metrics_registry
from webapi import oidc_auth
from webapi.admin_auth import (
    ADMIN_CSRF_COOKIE,
    ADMIN_SESSION_COOKIE,
    admin_auth_enabled,
    csrf_token_for,
    enforce_admin_token,
    issue_admin_session,
    require_admin_auth,
    require_global,
    require_scope,
    revoke_admin_session,
    revoke_all_admin_sessions,
    token_scope,
)
from webapi.chat_support import _bounded_id_list, _bounded_text
from webapi.rate_limit import FixedWindowRateLimiter

router = APIRouter()

_STATIC_DIR = admin_common.STATIC_DIR
_request_scope = admin_common.request_scope
_audit_admin = admin_common.audit_admin
_admin_page_html = admin_common.admin_page_html

_REVIEW_ITEM_FIELDS = (
    "review_id",
    "priority",
    "status",
    "tenant_id",
    "user_query",
    "answer_mode",
    "confidence_route",
    "decision_route",
    "candidate_ids",
    "selected_candidate_id",
    "feedback_type",
    "bad_reason",
    "created_at",
    "source",
    "reasons",
)


def _review_queue_path() -> Path:
    return Path(config.RUNS_DIR) / "review" / "review_queue.jsonl"


def _bounded_review_item(record: Dict[str, Any]) -> Dict[str, Any]:
    item: Dict[str, Any] = {}
    for field in _REVIEW_ITEM_FIELDS:
        value = record.get(field)
        if field == "candidate_ids":
            item[field] = _bounded_id_list(value, limit=20)
        elif field == "reasons":
            item[field] = [
                bounded
                for raw in (value if isinstance(value, list) else [])
                if (bounded := _bounded_text(raw, 300))
            ][:8]
        elif field == "user_query":
            item[field] = _bounded_text(value, 500)
        elif field == "bad_reason":
            item[field] = _bounded_text(value, 300)
        else:
            item[field] = _bounded_text(value, 1000)
    if not item.get("tenant_id"):
        item["tenant_id"] = "default"
    if not item.get("status"):
        item["status"] = "open"
    return item


def _load_review_items(
    *,
    status: str | None = None,
    priority: str | None = None,
    tenant_id: str | None = None,
    limit: int = 100,
    tenant_scope=None,
) -> Dict[str, Any]:
    source_path = _review_queue_path()
    safe_limit = max(0, min(int(limit or 100), 500))
    filters = {
        "status": status,
        "priority": priority,
        "tenant_id": tenant_id,
        "limit": safe_limit,
    }
    total_loaded = 0
    skipped_malformed = 0
    items: List[Dict[str, Any]] = []
    # Latest operator decision per (tenant, review_id) overrides the status
    # written when the queue was built.
    action_statuses = latest_review_statuses(Path(config.RUNS_DIR) / "review" / "review_actions.jsonl")

    if source_path.exists():
        with source_path.open("r", encoding="utf-8") as f:
            for line in f:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    record = json.loads(raw)
                except json.JSONDecodeError:
                    skipped_malformed += 1
                    continue
                if not isinstance(record, dict):
                    skipped_malformed += 1
                    continue
                total_loaded += 1
                item = _bounded_review_item(record)
                decided = action_statuses.get((str(item.get("tenant_id") or "default"), str(item.get("review_id") or "")))
                if decided:
                    item["status"] = decided
                if status and item.get("status") != status:
                    continue
                if priority and item.get("priority") != priority:
                    continue
                if tenant_id and item.get("tenant_id") != tenant_id:
                    continue
                if tenant_scope is not None and str(item.get("tenant_id") or "default") not in tenant_scope:
                    continue  # stage 15: a tenant administrator sees only its tenants
                if len(items) < safe_limit:
                    items.append(item)

    return {
        "items": items,
        "total_loaded": total_loaded,
        "returned_count": len(items),
        "skipped_malformed_lines": skipped_malformed,
        "source_path": str(source_path),
        "filters": filters,
    }


# --- Admin browser login (stage 9) -------------------------------------------
# Admin auth is fail-closed and browsers cannot send X-Admin-Token on a page
# navigation, so the admin pages were unreachable from a browser. /admin/login
# exchanges the token for an httponly, SameSite=Strict session cookie (signed
# with the token; ADMIN_SESSION_TTL_SECONDS, default 8h).


class AdminLoginRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=512)


_ADMIN_LOGIN_LIMITER = FixedWindowRateLimiter()


def _admin_login_attempts_per_minute() -> int:
    try:
        return max(1, int(os.getenv("ADMIN_LOGIN_ATTEMPTS_PER_MINUTE", "") or 10))
    except ValueError:
        return 10


@router.get("/admin/login", response_class=HTMLResponse)
def admin_login_page():
    path = _STATIC_DIR / "admin_login.html"
    try:
        return HTMLResponse(path.read_text(encoding="utf-8"))
    except Exception:
        logging.exception("admin login page unavailable")
        raise HTTPException(status_code=500, detail="admin login page unavailable")


@router.post("/admin/login")
def admin_login(req: AdminLoginRequest, request: Request):
    if not admin_auth_enabled():
        raise HTTPException(status_code=404, detail="not found")
    # Always limited, independent of RATE_LIMIT_ENABLED: one bucket per address.
    host = request.client.host if request.client else "unknown"
    bucket = "admin-login:" + hashlib.sha256(host.encode("utf-8")).hexdigest()[:12]
    allowed, retry_after = _ADMIN_LOGIN_LIMITER.check(bucket, _admin_login_attempts_per_minute())
    if not allowed:
        raise HTTPException(
            status_code=429, detail="too many login attempts", headers={"Retry-After": str(max(1, int(retry_after) + 1))}
        )
    try:
        enforce_admin_token({"x-admin-token": req.token})
    except HTTPException:
        metrics_registry.increment("api_auth_rejection_total", "admin_login_failed")
        _audit_admin(frozenset(), "admin_login_failed")
        raise
    scope = token_scope(req.token)[1]
    cookie, ttl = issue_admin_session(scope=scope)
    _audit_admin(scope, "admin_login")
    resp = JSONResponse({"ok": True, "expires_in": ttl})
    secure = oidc_auth.cookie_secure()
    resp.set_cookie(
        ADMIN_SESSION_COOKIE, cookie, max_age=ttl, httponly=True,
        samesite="strict", secure=secure, path="/",
    )
    # Readable by the admin pages' script, which echoes it in X-CSRF-Token.
    resp.set_cookie(
        ADMIN_CSRF_COOKIE, csrf_token_for(cookie), max_age=ttl, httponly=False,
        samesite="strict", secure=secure, path="/",
    )
    return resp


@router.post("/admin/logout")
def admin_logout(request: Request):
    # The session is revoked on the server too: a copied cookie stops working.
    revoke_admin_session(request.cookies.get(ADMIN_SESSION_COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(ADMIN_SESSION_COOKIE, path="/")
    resp.delete_cookie(ADMIN_CSRF_COOKIE, path="/")
    return resp


@router.post("/admin/logout-all")
def admin_logout_all(request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    # Ends every admin browser session issued until now, on every node.
    scope = _request_scope(request)
    require_global(scope)
    revoke_all_admin_sessions()
    _audit_admin(scope, "admin_logout_all")
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(ADMIN_SESSION_COOKIE, path="/")
    resp.delete_cookie(ADMIN_CSRF_COOKIE, path="/")
    return resp


@router.get("/admin", response_class=HTMLResponse)
def admin_home_page(_admin_auth: None = Depends(require_admin_auth)):
    path = _STATIC_DIR / "admin_home.html"
    try:
        return HTMLResponse(_admin_page_html(path))
    except Exception:
        logging.exception("admin dashboard unavailable")
        raise HTTPException(status_code=500, detail="admin dashboard unavailable")


@router.get("/admin/metrics/summary")
def admin_metrics_summary(_admin_auth: None = Depends(require_admin_auth)):
    from webapi.alerting import evaluate_alerts

    counters = metrics_registry.aggregate_snapshot()
    audit_failures = write_failure_count()
    if audit_failures:
        counters = dict(counters)
        counters["audit_write_failures_total"] = {"total": audit_failures}
    return {"counters": counters, "scope": metrics_registry.scope(), "alerts": evaluate_alerts({"counters": counters})}


@router.get("/admin/review", response_class=HTMLResponse)
def admin_review_page(_admin_auth: None = Depends(require_admin_auth)):
    path = _STATIC_DIR / "review_queue.html"
    try:
        return HTMLResponse(_admin_page_html(path))
    except Exception:
        logging.exception("review queue page unavailable")
        raise HTTPException(status_code=500, detail="review queue page unavailable")


@router.get("/admin/review/items")
def admin_review_items(
    request: Request = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    tenant_id: Optional[str] = None,
    limit: int = 100,
    _admin_auth: None = Depends(require_admin_auth),
):
    scope = _request_scope(request)
    if tenant_id:
        require_scope(scope, tenant_id)
    return _load_review_items(
        status=status,
        priority=priority,
        tenant_id=tenant_id,
        limit=limit,
        tenant_scope=scope,
    )
