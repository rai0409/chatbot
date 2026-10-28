from __future__ import annotations

"""Deployment mode and start-up safety checks (6-1, 6-2, 6-9, v2_07).

APP_ENV=production switches the API to its hardened behaviour (minimal
/health, protected /metrics, no /docs, no anonymous conversation history)
and refuses to start when a setting is unsafe for a customer deployment.
Development (APP_ENV unset or "development") keeps the historical
behaviour so local runs and tests are unchanged.
"""

import os
from typing import List

_ENABLED_VALUES = {"1", "true", "yes", "on"}
_MIN_SECRET_BYTES = 32


def _env(name: str) -> str:
    return str(os.getenv(name, "")).strip()


def _enabled(name: str) -> bool:
    return _env(name).lower() in _ENABLED_VALUES


def app_env() -> str:
    return _env("APP_ENV").lower() or "development"


def is_production() -> bool:
    return app_env() == "production"


def docs_enabled() -> bool:
    # /docs, /redoc and /openapi.json describe every route; off in production
    # unless API_DOCS_ENABLED=true.
    if _env("API_DOCS_ENABLED"):
        return _enabled("API_DOCS_ENABLED")
    return not is_production()


def production_problems() -> List[str]:
    """Settings that must not be used in production. Empty list = OK."""
    problems: List[str] = []
    if not _enabled("API_AUTH_ENABLED") and not _enabled("ENTERPRISE_OIDC_ENABLED") and not _enabled("ENTERPRISE_AUTH_ENABLED"):
        problems.append("no API authentication (API_AUTH_ENABLED / ENTERPRISE_OIDC_ENABLED / ENTERPRISE_AUTH_ENABLED)")
    if _enabled("API_AUTH_ALLOW_ANY_TENANT_WITHOUT_MAP") or _enabled("OIDC_ALLOW_ANY_TENANT_WITHOUT_MAP"):
        problems.append("*_ALLOW_ANY_TENANT_WITHOUT_MAP must not be enabled")
    if not _enabled("ADMIN_AUTH_ENABLED") or len(_env("ADMIN_AUTH_TOKEN")) < _MIN_SECRET_BYTES:
        problems.append(f"ADMIN_AUTH_ENABLED=true with an ADMIN_AUTH_TOKEN of at least {_MIN_SECRET_BYTES} characters")
    if _enabled("ADMIN_AUTH_ALLOW_INSECURE_DEV"):
        problems.append("ADMIN_AUTH_ALLOW_INSECURE_DEV must not be enabled")
    if _env("SEARCH_DEBUG_ENABLED").lower() in {"", *_ENABLED_VALUES}:
        problems.append("SEARCH_DEBUG_ENABLED=false (the debug route exposes retrieval internals)")
    if not _env("ALLOWED_HOSTS"):
        problems.append("ALLOWED_HOSTS (comma separated host names served by this API)")
    if not _env("METRICS_TOKEN") or len(_env("METRICS_TOKEN")) < _MIN_SECRET_BYTES:
        problems.append(f"METRICS_TOKEN of at least {_MIN_SECRET_BYTES} characters (/metrics requires it)")
    if _enabled("ENTERPRISE_OIDC_ENABLED") and len(_env("OIDC_SESSION_SECRET").encode("utf-8")) < _MIN_SECRET_BYTES:
        problems.append(f"OIDC_SESSION_SECRET of at least {_MIN_SECRET_BYTES} bytes")
    if _env("OIDC_COOKIE_SECURE").lower() in {"0", "false", "no", "off"}:
        problems.append("OIDC_COOKIE_SECURE must not be false")
    if _enabled("DISABLE_GUARD"):
        problems.append("DISABLE_GUARD must not be enabled")
    if not _enabled("TENANT_FILTER_STRICT"):
        problems.append(
            "TENANT_FILTER_STRICT=true (run scripts/backfill_tenant_id.py --apply on existing collections first)"
        )
    if not _enabled("EMBED_FINGERPRINT_STRICT"):
        problems.append("EMBED_FINGERPRINT_STRICT=true (a collection without a complete embedding stamp must be refused)")
    if _env("CORS_ALLOW_ORIGINS") == "*":
        problems.append("CORS_ALLOW_ORIGINS must list origins, not *")
    if _env("ALLOWED_HOSTS") == "*" or "*" in [h.strip() for h in _env("ALLOWED_HOSTS").split(",")]:
        problems.append("ALLOWED_HOSTS must list host names, not *")
    # Stage 14: Chroma's local store is not safe for several processes (one
    # worker writing while others read sees stale or broken data).
    try:
        workers = int(_env("WEB_CONCURRENCY") or 1)
    except ValueError:
        workers = 1
    if workers > 1 and (_env("VECTOR_BACKEND") or "chroma").lower() == "chroma":
        problems.append("VECTOR_BACKEND=qdrant when WEB_CONCURRENCY > 1 (Chroma is single-process)")
    from rag_core import llm

    llm_problem = llm.production_problem()
    if llm_problem:
        problems.append(llm_problem)
    return problems


def check_production_settings() -> None:
    """Raise RuntimeError listing every unsafe setting (production only)."""
    if not is_production():
        return
    problems = production_problems()
    if problems:
        raise RuntimeError("APP_ENV=production refused to start; fix: " + "; ".join(problems))
