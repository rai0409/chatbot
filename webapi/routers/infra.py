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
from webapi.backups import _float_env
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


def _index_readiness() -> Dict[str, Any]:
    """Generation pointer readability and the embedding fingerprint of the served generation.

    A corrupt pointer always fails readiness (queries would fail closed); a fingerprint that differs from the
    served manifest fails it in production (vectors and queries would not be comparable)."""
    from rag_core import index_generation

    failing = []
    pointer = index_generation.pointer_status()
    if not pointer["ok"]:
        failing.append("generation_pointer")
    try:
        fingerprint = index_generation.fingerprint_status()
    except Exception as exc:  # noqa: BLE001  reported by exception type name only
        fingerprint = {"ok": False, "detail": type(exc).__name__}
    state = {True: "match", False: "mismatch", None: "no_manifest"}.get(fingerprint.get("ok"), "unknown")
    if fingerprint.get("ok") is False and production_guard.is_production():
        failing.append("embedding_fingerprint")
    return {"failing": failing, "fingerprint": state}


@router.get("/ready")
def ready():
    # Readiness for a load balancer / orchestrator: 503 while a configured
    # shared service (Redis, Postgres, Qdrant) is unreachable, the generation
    # pointer is unreadable, or (production) the embedding fingerprint differs
    # from the served generation's manifest. Names only.
    deps = {**state_backend.health(), **vector_backend.health()}
    failing = sorted(name for name, state in deps.items() if not state.get("ok"))
    try:
        index = _index_readiness()
    except Exception:  # noqa: BLE001  index check failure is logged and reported as failing
        logging.exception("index readiness check failed")
        index = {"failing": ["index_check"], "fingerprint": "unknown"}
    failing += index["failing"]
    if failing:
        return JSONResponse(status_code=503, content={"status": "not_ready", "failing": failing,
                                                      "fingerprint": index["fingerprint"]})
    return {"status": "ready", "fingerprint": index["fingerprint"]}


def ops_state() -> Dict[str, Any]:
    """Operational state for monitoring (counts and names of collections only; never document text).

    Each part is independent: a failing part is reported as an error, never fails /metrics."""
    import shutil

    state: Dict[str, Any] = {}

    def part(name, fn):
        try:
            state[name] = fn()
        except Exception as exc:  # noqa: BLE001  each part is reported as an error, never fails /metrics
            state[name] = {"error": type(exc).__name__}

    def registry():
        from webapi import ingestion_jobs

        if not ingestion_jobs._pg_registry() and not ingestion_jobs.registry_path().exists():
            return {"collections_stuck_writing": 0, "operations_running": 0, "operations_failed": 0,
                    "operations_interrupted": 0, "tombstones": 0}
        stuck_minutes = _float_env("OPS_WRITING_STUCK_MINUTES", 60)
        lookback = time.time() - _float_env("OPS_LOOKBACK_HOURS", 24) * 3600
        ops = [o for o in ingestion_jobs.list_operations(limit=500) if float(o.get("created_at") or 0) >= lookback]
        return {
            "collections_stuck_writing": len(ingestion_jobs.collections_stuck_writing(stuck_minutes * 60)),
            "operations_running": sum(1 for o in ops if o.get("status") == ingestion_jobs.OP_RUNNING),
            "operations_failed": sum(1 for o in ops if o.get("status") == ingestion_jobs.OP_FAILED),
            "operations_interrupted": sum(1 for o in ops if o.get("status") == ingestion_jobs.OP_INTERRUPTED),
            "tombstones": len(ingestion_jobs.list_tombstones()),
        }

    def sources():
        from rag_core import change_ledger

        try:
            change_ledger.verify_chain()
            chain_ok = True
        except change_ledger.LedgerError:
            chain_ok = False
        latest: Dict[str, str] = {}
        for entry in change_ledger.read_entries():
            if entry.get("kind") in ("apply", "apply_failed", "hold"):
                latest[str(entry.get("source_id"))] = str(entry.get("kind"))
        return {"ledger_chain_ok": chain_ok,
                "apply_failed": sum(1 for k in latest.values() if k == "apply_failed"),
                "held": sum(1 for k in latest.values() if k == "hold")}

    def index():
        from rag_core import index_generation
        from rag_core.retrieval import keyword_index_status

        fingerprint = index_generation.fingerprint_status()
        out = {"pointer_ok": index_generation.pointer_status()["ok"],
               "fingerprint": {True: "match", False: "mismatch", None: "no_manifest"}.get(fingerprint.get("ok"), "unknown"),
               "manifest_status": fingerprint.get("manifest_status")}
        if fingerprint.get("manifest_status") == "verified":
            from rag_core import store

            vectors = store.get_vectorstore(create_if_missing=False, verify_embedding_fingerprint=False)
            live_points = int(vectors.count())
            live_keyword = int(keyword_index_status().get("keyword_index_records") or 0)
            out["count_drift"] = (live_points != fingerprint.get("point_count")
                                  or live_keyword != fingerprint.get("keyword_rows"))
        return out

    def backup():
        from webapi import backups

        return backups.status_summary()

    def disk():
        used = 0.0
        for path in {config.BASE_DIR, config.VECTORSTORE_DIR, config.INDEX_DIR}:
            try:
                usage = shutil.disk_usage(str(path))
            except OSError:
                continue
            used = max(used, usage.used / usage.total if usage.total else 0.0)
        return {"used_fraction": round(used, 4)}

    part("registry", registry)
    part("sources", sources)
    part("index", index)
    part("backup", backup)
    part("disk", disk)
    return state


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
    ops_raw = os.getenv("METRICS_OPS_STATE")
    if ops_raw is None or not ops_raw.strip() or config._get_bool("METRICS_OPS_STATE", default=True):
        payload["ops"] = ops_state()
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
