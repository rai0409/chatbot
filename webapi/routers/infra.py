"""Liveness, readiness, metrics, static pages and UI context (moved from main.py, S7)."""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

import config
from rag_core import embedding_provider
from rag_core import vector_backend
from rag_core.audit_log import write_failure_count
from rag_core.qa import distance_guard_calibration
from rag_core.retrieval import keyword_index_status
from webapi import admin_common
from webapi import branding
from webapi import metrics_registry
from webapi import oidc_auth
from webapi import production_guard
from webapi import request_stats
from webapi import state_backend
from webapi.admin_auth import admin_auth_enabled, require_admin_auth
from webapi.api_auth import api_auth_enabled

router = APIRouter()

_STATIC_DIR = admin_common.STATIC_DIR


def _health_details() -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "status": "ok",
        "chroma_collection": config.resolve_chroma_collection_name(),
        "vectorstore_collection_name": config.VECTORSTORE_COLLECTION_NAME,
        "vectorstore_dir": config.VECTORSTORE_DIR,
        "chat_generation_mode": config.resolve_chat_generation_mode(),
    }
    from rag_core import index_generation

    active_generation = index_generation.active_pointer()
    if active_generation:
        payload["active_generation"] = {
            "collection": active_generation.get("collection"),
            "generation_id": active_generation.get("generation_id"),
            "activated_at": active_generation.get("activated_at"),
        }
    try:
        payload["distance_guard"] = distance_guard_calibration()
    except Exception:  # noqa: BLE001
        logging.exception("distance guard calibration status unavailable")
    try:
        payload.update(keyword_index_status())
    except Exception:
        logging.exception("keyword index status unavailable")
        payload.update(
            {
                "keyword_index_loaded": False,
                "keyword_index_records": 0,
                "keyword_index_path": str(config.CHUNKS_JSONL_PATH),
            }
        )
    try:
        payload.update(embedding_provider.active_fingerprint())
    except Exception:
        logging.exception("embedding fingerprint unavailable")
        payload.update({"embed_provider": None, "embed_model": None})
    return payload


@router.get("/health")
def health():
    # Production: liveness only. Paths, collection names and models are on
    # the admin-only /admin/health.
    if production_guard.is_production():
        return {"status": "ok"}
    return _health_details()


@router.get("/admin/health")
def admin_health(_admin_auth: None = Depends(require_admin_auth)):
    payload = _health_details()
    # Reachability of Redis / Postgres / Qdrant when they are configured (stage 10).
    payload["dependencies"] = {**state_backend.health(), **vector_backend.health()}
    return payload


@router.get("/ready")
def ready():
    # Readiness for a load balancer / orchestrator: 503 while a configured
    # shared service (Redis, Postgres, Qdrant) is unreachable. Names only.
    deps = {**state_backend.health(), **vector_backend.health()}
    failing = sorted(name for name, state in deps.items() if not state.get("ok"))
    if failing:
        return JSONResponse(status_code=503, content={"status": "not_ready", "failing": failing})
    return {"status": "ready"}


def require_metrics_access(request: Request) -> None:
    # Production: /metrics needs "Authorization: Bearer <METRICS_TOKEN>".
    if not production_guard.is_production():
        return
    import hmac

    expected = str(os.getenv("METRICS_TOKEN", "")).strip()
    header = str(request.headers.get("authorization") or "")
    provided = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not expected:
        raise HTTPException(status_code=503, detail="metrics token is not configured")
    if not provided:
        raise HTTPException(status_code=401, detail="metrics authentication required")
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="invalid metrics credentials")


@router.get("/metrics")
def metrics(format: Optional[str] = None, _metrics_auth: None = Depends(require_metrics_access)):
    # Counters are per-process; with multiple workers each process reports
    # its own numbers. Default output is JSON; ?format=prometheus returns the
    # Prometheus text exposition format generated from the same counters.
    payload = {
        "uptime_seconds": int(time.time() - request_stats.start_time),
        "total_requests": request_stats.total_requests,
        "error_requests": request_stats.error_requests,
        "counters": metrics_registry.aggregate_snapshot(),
        "counters_scope": metrics_registry.scope(),
    }
    if metrics_registry.scope() == "all_workers":
        totals = metrics_registry.aggregate_totals()
        payload["total_requests"] = totals["requests"]
        payload["error_requests"] = totals["errors"]
    audit_failures = write_failure_count()
    if audit_failures:
        # Audit writes that failed (disk full, permissions): alert on > 0.
        payload["counters"] = dict(payload["counters"])
        payload["counters"]["audit_write_failures_total"] = {"total": audit_failures}
    if str(format or "").strip().lower() == "prometheus":
        return PlainTextResponse(
            metrics_registry.to_prometheus(payload),
            media_type=metrics_registry.PROMETHEUS_CONTENT_TYPE,
        )
    return payload


@router.get("/product-preview", response_class=HTMLResponse)
def product_preview_page():
    path = _STATIC_DIR / "product_preview.html"
    try:
        return HTMLResponse(path.read_text(encoding="utf-8"))
    except Exception:
        logging.exception("product preview page unavailable")
        raise HTTPException(status_code=500, detail="product preview page unavailable")


# Scripts shared by the pages (stage 13; unit-tested with node --test).
_STATIC_SCRIPTS = {"render.js", "login.js"}


@router.get("/static/js/{name}")
def static_script(name: str):
    if name not in _STATIC_SCRIPTS:
        raise HTTPException(status_code=404, detail="not found")
    return PlainTextResponse(
        (_STATIC_DIR / "js" / name).read_text(encoding="utf-8"),
        media_type="application/javascript; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/chat-ui", response_class=HTMLResponse)
def chat_ui_page():
    # Serves the minimal end-user chat UI (static shell only). The page calls
    # the existing /chat/stream and /chat/feedback endpoints, which keep their
    # own API-auth / tenant-authorization / rate-limit enforcement; this route
    # adds no new auth scheme and carries no secrets.
    path = _STATIC_DIR / "chat.html"
    try:
        return HTMLResponse(path.read_text(encoding="utf-8"))
    except Exception:
        logging.exception("chat ui page unavailable")
        raise HTTPException(status_code=500, detail="chat ui page unavailable")


@router.get("/branding")
def branding_endpoint():
    # Safe, secret-free display config for the workspace (logo text, product
    # name, subtitle, theme color). No API keys, tokens, or tenant data.
    return branding.branding_config()


@router.get("/ui/context")
def ui_context(request: Request):
    # Backend-authoritative UI context. Role is used by the UI only to show/hide
    # panels; privileged routes (/admin/review*) are independently enforced by
    # require_admin_auth. No secrets / raw keys are returned.
    # Prefer an authenticated OIDC session role (Prompt047) when present;
    # otherwise fall back to the admin-token-based role. Backend-authoritative.
    oidc_ctx = oidc_auth.resolve_oidc_session(request.headers)
    role = (oidc_ctx.role if (oidc_ctx and oidc_ctx.role) else None) or branding.resolve_role(request.headers)
    return {
        "role": role,
        "admin_auth_enabled": admin_auth_enabled(),
        "api_auth_enabled": api_auth_enabled(),
    }
