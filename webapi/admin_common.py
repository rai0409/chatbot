"""What the administrative routes share (S1): the request's scope, the audit of an admin change, the shared page header.

These lived in main.py. The routers (webapi/routers) import them from here, so a router never imports main.py;
main.py keeps its old names as aliases of these functions.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Request

from rag_core.audit_log import append_audit_event
from webapi.admin_auth import admin_scope

STATIC_DIR = Path(__file__).resolve().parent / "static"


def request_scope(request: Optional[Request]):
    # Direct function calls (tests) bypass FastAPI and its admin dependency,
    # like enforce_tenant_authorization; served requests always carry one.
    return admin_scope(request.headers) if isinstance(request, Request) else None


def admin_actor(scope) -> Dict[str, Any]:
    # Audit actor of an admin request: never the token, only its reach.
    return {"admin_scope": "installation" if scope is None else sorted(scope)}


def audit_admin(scope, action: str, **fields: Any) -> None:
    """Stage 15: administrative changes are written to the audit log."""
    append_audit_event("admin", {"action": action, **admin_actor(scope), **fields})


def admin_label(scope) -> str:
    return "installation" if scope is None else ",".join(sorted(scope))


def admin_page_html(path: Path) -> str:
    # Admin pages share one header (menu, logout, CSRF-aware fetch); it is
    # inserted where a page has the "admin-nav" marker comment.
    html = path.read_text(encoding="utf-8")
    marker = "<!--admin-nav-->"
    if marker in html:
        try:
            nav = (STATIC_DIR / "admin_nav.html").read_text(encoding="utf-8")
        except OSError:
            nav = ""
        html = html.replace(marker, nav, 1)
    return html


def allowed_tenants(scope) -> Optional[set]:
    """The tenants an administrator may see: None (every tenant) for the installation administrator, else their set."""
    return None if scope is None else set(scope)


def actor_name(raw: Optional[str]) -> str:
    """The name an administrator gives for a change that needs a person (a proposal, an approval, a judgement).

    The admin credential says which tenants the request may touch, not who is at the keyboard, so the console asks for a
    name. It is a statement, not a proof: it is kept in the record and in the audit log. 1 to 64 characters."""
    from fastapi import HTTPException

    name = str(raw or "").strip()
    if not name or len(name) > 64:
        raise HTTPException(status_code=400, detail="a name (1 to 64 characters) of the person who does this is required")
    return name


def actor_identity(request, name: Optional[str]) -> Dict[str, Any]:
    """Who is acting: the name typed in the console and, when the request carries a single sign-on session, that person's identifier.

    {"name", "id", "verified"}: `verified` is true only with a single sign-on session (the identifier is a hash of the person's
    subject, never the subject itself). A typed name alone is a statement, not a proof."""
    clean = actor_name(name)
    verified_id = None
    try:
        from webapi import oidc_auth

        session = oidc_auth.resolve_oidc_session(request.headers) if isinstance(request, Request) else None
        if session is not None and getattr(session, "key_fingerprint", None):
            verified_id = str(session.key_fingerprint)
    except Exception:  # noqa: BLE001 - no usable session: the name only
        verified_id = None
    return {"name": clean, "id": verified_id, "verified": verified_id is not None}
