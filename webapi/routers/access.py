"""Who may do what (S4): a read-only overview of the access settings of the installation.

There is no user table here: people and groups live in the identity provider (single sign-on) and in the environment. This shows
what the installation is set up to accept, so an administrator can check it. No key, token, group name or address is returned:
only whether a mechanism is on, how many entries it has, and which roles and tenants it knows.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Request

from webapi import admin_common
from webapi.admin_auth import require_admin_auth

router = APIRouter()
_ON = {"1", "true", "yes", "y", "on"}

ROLES = [
    {"name": "admin", "description": "管理者。すべての管理操作"},
    {"name": "operator", "description": "運用担当。取り込み・レビュー・契約などの日常の操作"},
    {"name": "user", "description": "利用者。質問と回答の利用"},
    {"name": "viewer", "description": "閲覧のみ"},
]


def _on(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in _ON


def _entries(name: str) -> List[str]:
    return [part.strip() for part in str(os.getenv(name, "")).split(",") if part.strip()]


def _mapped(name: str) -> Dict[str, Any]:
    """Count the 'left=right' entries of a mapping variable and list the distinct right-hand sides (roles or tenants)."""
    rights = sorted({entry.split("=", 1)[1].strip() for entry in _entries(name) if "=" in entry})
    return {"entries": len(_entries(name)), "values": rights}


@router.get("/admin/access")
def access_overview(request: Request, _admin_auth: None = Depends(require_admin_auth)):
    scope = admin_common.request_scope(request)
    tenant_tokens = [e.split("=", 1)[1].strip() for e in _entries("ADMIN_AUTH_TENANT_TOKENS") if "=" in e]
    return {
        "you": {"reach": "installation" if scope is None else "tenants", "tenants": None if scope is None else sorted(scope)},
        "roles": ROLES,
        "admin_auth": {"enabled": _on("ADMIN_AUTH_ENABLED"), "installation_credential": bool(os.getenv("ADMIN_AUTH_TOKEN")),
                       "tenant_credentials": len(tenant_tokens), "tenants_with_own_admin": sorted(set(tenant_tokens)),
                       "session_hours": round(int(os.getenv("ADMIN_SESSION_TTL_SECONDS", "") or 28800) / 3600, 1)},
        "api_auth": {"enabled": _on("API_AUTH_ENABLED"), "keys": len(_entries("API_AUTH_KEYS")),
                     "tenant_map": _mapped("API_AUTH_TENANT_MAP")},
        "single_sign_on": {"enabled": _on("ENTERPRISE_OIDC_ENABLED"), "group_role_map": _mapped("OIDC_GROUP_ROLE_MAP"),
                           "group_tenant_map": _mapped("OIDC_GROUP_TENANT_MAP")},
        "note": "利用者とグループは、シングルサインオンの ID 基盤と環境の設定で管理されます。ここは設定の確認用です（キー・グループ名は表示しません）。",
    }
