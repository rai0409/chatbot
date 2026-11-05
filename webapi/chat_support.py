"""Plain helpers the chat, search and review routes share (moved from main.py, S7).

Routers import these from here, so a router never imports main.py; main.py keeps the old names.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from webapi import ingestion_jobs


def _preview(text: Any, max_chars: int) -> str:
    value = str(text or "")
    if len(value) <= max_chars:
        return value
    suffix = "...[truncated]"
    if max_chars <= len(suffix):
        return value[:max_chars]
    return value[: max_chars - len(suffix)] + suffix


def _bounded_text(value: Any, max_chars: int = 1000) -> str | None:
    if value is None:
        return None
    return _preview(str(value), max_chars)


def _bounded_id_list(values: Any, limit: int = 20) -> List[str]:
    if not isinstance(values, list):
        return []
    out: List[str] = []
    for value in values[:limit]:
        text = _bounded_text(value, 1000)
        if text:
            out.append(text)
    return out


def _resolve_staging_collection(collection: Optional[str], *, tenant_id: str) -> Optional[str]:
    name = str(collection or "").strip()
    if not name:
        return None
    if ingestion_jobs.is_production_collection(name):
        raise HTTPException(status_code=400, detail="refusing production/default collection for staging query")
    try:
        ingestion_jobs.validate_collection_name(name)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid staging collection name")
    status = ingestion_jobs.staging_collection_status(name, tenant_id=tenant_id)
    if status.get("allowed"):
        return name
    reason = str(status.get("reason") or "staging_collection_unavailable")
    if reason == "staging_collection_forbidden_for_tenant":
        raise HTTPException(status_code=403, detail="staging collection is not authorized for this tenant")
    if reason == "staging_collection_unknown":
        # Unregistered collections are refused even if they exist in the
        # vectorstore: their owner tenant is unknown.
        raise HTTPException(status_code=404, detail="staging collection is not available for query")
    raise HTTPException(status_code=409, detail="staging collection is not ready for query")


def _collection_meta(collection_name: Optional[str]) -> Dict[str, Any]:
    if not collection_name:
        return {"query_collection_mode": "served_default", "query_collection": None}
    return {"query_collection_mode": "staging", "query_collection": collection_name}
