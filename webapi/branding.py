from __future__ import annotations

# Safe, secret-free customer branding + backend-authoritative role resolution
# (Prompt043). Branding comes from non-secret env vars with safe defaults;
# nothing here exposes API keys, tokens, or tenant-private data.
#
# Role resolution is AUTHORITATIVE on the server. The UI uses it only to
# show/hide panels (cosmetic); privileged routes (e.g. /admin/review*) remain
# independently enforced by require_admin_auth. Frontend gating never grants
# access by itself.

import os
from collections.abc import Mapping
from typing import Dict

from webapi.admin_auth import admin_auth_enabled, enforce_admin_token

ROLE_ADMIN = "admin"
ROLE_USER = "user"

_DEFAULTS = {
    "product_name": "蔵伝 / KuraDen",
    "subtitle": "社内ナレッジ アシスタント（オンプレ版）",
    "theme_color": "#0b6b5b",
    "logo_text": "蔵伝",
}


def _env(name: str, default: str) -> str:
    value = str(os.getenv(name, "") or "").strip()
    return value[:120] if value else default


def branding_config() -> Dict[str, str]:
    # All fields are non-secret display strings.
    return {
        "product_name": _env("BRANDING_PRODUCT_NAME", _DEFAULTS["product_name"]),
        "subtitle": _env("BRANDING_SUBTITLE", _DEFAULTS["subtitle"]),
        "theme_color": _env("BRANDING_THEME_COLOR", _DEFAULTS["theme_color"]),
        "logo_text": _env("BRANDING_LOGO_TEXT", _DEFAULTS["logo_text"]),
    }


def resolve_role(headers: Mapping[str, str] | None) -> str:
    # Authoritative role for UI gating, mirroring real admin access:
    # - admin auth disabled  -> admin routes are open (dev/open mode) -> "admin"
    # - admin auth enabled    -> "admin" only with a valid admin token, else "user"
    if not admin_auth_enabled():
        return ROLE_ADMIN
    try:
        enforce_admin_token(headers)
        return ROLE_ADMIN
    except Exception:
        return ROLE_USER
