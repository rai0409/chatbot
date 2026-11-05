"""OIDC browser login / logout and the revocation of an identity's sessions (moved from main.py, S7)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from webapi import admin_common
from webapi import oidc_auth
from webapi.admin_auth import require_admin_auth, require_global

router = APIRouter()

_request_scope = admin_common.request_scope
_audit_admin = admin_common.audit_admin


def _require_oidc() -> None:
    if not oidc_auth.oidc_enabled():
        raise HTTPException(status_code=404, detail="not found")


@router.get("/auth/oidc/login")
def oidc_login():
    # Default-off (404 when disabled). Builds the Authorization Code + PKCE
    # redirect and binds state/nonce/verifier in a signed, httponly txn cookie.
    _require_oidc()
    auth_url, txn = oidc_auth.build_login()
    resp = RedirectResponse(url=auth_url, status_code=302)
    resp.set_cookie(
        oidc_auth.TXN_COOKIE, txn, max_age=600, httponly=True,
        samesite="lax", secure=oidc_auth.cookie_secure(), path="/auth/oidc",
    )
    return resp


@router.get("/auth/oidc/callback")
def oidc_callback(request: Request, code: Optional[str] = None, state: Optional[str] = None):
    _require_oidc()
    if not code or not state:
        raise HTTPException(status_code=400, detail="missing code/state")
    txn_cookie = request.cookies.get(oidc_auth.TXN_COOKIE) or ""
    session = oidc_auth.complete_login(code=code, state=state, txn_cookie=txn_cookie)
    resp = RedirectResponse(url="/chat-ui", status_code=302)
    resp.set_cookie(
        oidc_auth.SESSION_COOKIE, session, max_age=3600, httponly=True,
        samesite="lax", secure=oidc_auth.cookie_secure(), path="/",
    )
    resp.delete_cookie(oidc_auth.TXN_COOKIE, path="/auth/oidc")
    return resp


@router.get("/auth/oidc/logout")
@router.post("/auth/oidc/logout")
def oidc_logout(request: Request):
    _require_oidc()
    # Revoke server-side too: a copied cookie stops working after logout.
    oidc_auth.revoke_session_from_headers(request.headers)
    # end_session_url (when the IdP publishes one): the UI sends the browser
    # there so the IdP session ends too (RP-initiated logout).
    resp = JSONResponse({"ok": True, **oidc_auth.end_session_info()})
    resp.delete_cookie(oidc_auth.SESSION_COOKIE, path="/")
    return resp


# --- OIDC session revocation (6-3) ----------------------------------------------


class SessionRevokeRequest(BaseModel):
    key_fingerprint: str = Field(..., min_length=6, max_length=64)


@router.post("/admin/sessions/revoke")
def revoke_sessions(req: SessionRevokeRequest, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    # Every OIDC session of this identity issued until now stops working
    # (fingerprints are what audit records show as actor.key_fingerprint).
    scope = _request_scope(request)
    require_global(scope)
    oidc_auth.revoke_identity(req.key_fingerprint)
    _audit_admin(scope, "sessions_revoked", key_fingerprint=req.key_fingerprint)
    return {"ok": True, "key_fingerprint": req.key_fingerprint}
