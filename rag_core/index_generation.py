"""Which index generation is served.

The pointer file (INDEX_DIR/active_generation.json, or ACTIVE_GENERATION_FILE)
names the collection and keyword corpus that queries use. Without the file, or
while CHROMA_COLLECTION is set, nothing changes: the served name and corpus come
from the environment and CHUNKS_JSONL_PATH as before.

activate() writes the pointer only after verify_generation() passes every check;
rollback() returns to the previous generation after the same checks. Neither
touches the generations themselves, so the old one stays complete.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import config

POINTER_SCHEMA = 1
HISTORY_LIMIT = 20

_LOCK = threading.Lock()
_CACHE: Dict[str, Any] = {"key": None, "value": None}


class PointerError(RuntimeError):
    """The pointer file exists but cannot be used. Queries fail instead of silently serving another index."""


class GenerationError(RuntimeError):
    def __init__(self, message: str, report: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.report = report


def pointer_path() -> Path:
    raw = str(os.getenv("ACTIVE_GENERATION_FILE", "")).strip()
    return Path(raw) if raw else Path(config.INDEX_DIR) / "active_generation.json"


def _env_override() -> bool:
    return bool(str(os.getenv("CHROMA_COLLECTION") or "").strip())


def _validate(data: Any, path: Path) -> Dict[str, Any]:
    if not isinstance(data, dict) or data.get("schema_version") != POINTER_SCHEMA:
        raise PointerError(f"active generation file {path} has an unsupported schema")
    active = data.get("active")
    if active is not None and not (
        isinstance(active, dict)
        and str(active.get("collection") or "").strip()
        and str(active.get("keyword_index_path") or "").strip()
    ):
        raise PointerError(f"active generation file {path} has an invalid 'active' entry")
    if not isinstance(data.get("history", []), list):
        raise PointerError(f"active generation file {path} has an invalid 'history'")
    return data


def _load() -> Optional[Dict[str, Any]]:
    path = pointer_path()
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    key = (str(path), stat.st_mtime_ns, stat.st_size, getattr(stat, "st_ino", 0))
    with _LOCK:
        if _CACHE["key"] == key:
            return _CACHE["value"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PointerError(f"active generation file {path} is unreadable: {exc}") from exc
    _validate(data, path)
    with _LOCK:
        _CACHE["key"], _CACHE["value"] = key, data
    return data


def active_pointer() -> Optional[Dict[str, Any]]:
    """The active generation entry, or None (no pointer, cleared, or CHROMA_COLLECTION overrides it)."""
    if _env_override():
        return None
    data = _load()
    active = (data or {}).get("active")
    return dict(active) if active else None


def active_collection() -> str:
    pointer = active_pointer()
    return str(pointer["collection"]) if pointer else ""


def served_keyword_path() -> str:
    pointer = active_pointer()
    if pointer:
        return str(pointer["keyword_index_path"])
    return str(getattr(config, "CHUNKS_JSONL_PATH", "") or "")


def _raw_state() -> Dict[str, Any]:
    data = _load()
    return dict(data) if data else {"schema_version": POINTER_SCHEMA, "active": None, "history": []}


def _write_pointer(data: Dict[str, Any]) -> None:
    path = pointer_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    with _LOCK:
        _CACHE["key"] = None
        _CACHE["value"] = None


# --- verification ---------------------------------------------------------------------------------


def verify_generation(
    collection_name: str, *, provider_name: Optional[str] = None, update_manifest: bool = False
) -> Dict[str, Any]:
    """Every check a generation must pass before it is served. Never raises for a failed check."""
    from rag_core import embedding_fingerprint, index_manifest, store
    from scripts import check_index_consistency

    name = str(collection_name or "").strip()
    checks: List[Dict[str, Any]] = []

    def add(check: str, ok: bool, detail: Any = "") -> None:
        checks.append({"name": check, "ok": bool(ok), "detail": str(detail)})

    def finish(live: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
        return {"collection": name, "ok": all(c["ok"] for c in checks), "checks": checks, "live": live or {}}

    try:
        manifest = index_manifest.read_manifest(name)
    except ValueError as exc:
        add("manifest", False, exc)
        return finish()
    add("manifest", manifest is not None, "" if manifest is not None else "missing: register the generation first")
    if manifest is None:
        return finish()
    try:
        collection = store.get_vectorstore(
            collection_name=name, create_if_missing=False, verify_embedding_fingerprint=False
        )
    except Exception as exc:  # noqa: BLE001
        add("collection", False, f"{type(exc).__name__}: {exc}")
        return finish()
    add("collection", True)

    stamped = index_manifest.collection_profile(collection)
    declared = manifest.get("profile") or {}
    differs = [k for k in index_manifest.PROFILE_KEYS if stamped.get(k) != declared.get(k)]
    add("profile_matches_manifest", not differs, f"differs: {differs}" if differs else "")
    runtime = embedding_fingerprint.build_collection_fingerprint_metadata(provider_name=provider_name)
    wrong = [
        k for k in index_manifest.PROFILE_KEYS if k != "embedding_dim" and str(stamped.get(k)) != str(runtime.get(k))
    ]
    add("profile_matches_runtime", not wrong, f"differs from the running provider: {wrong}" if wrong else "")

    live_points = int(collection.count())
    stamped_dim = stamped.get("embedding_dim")
    actual_dim = embedding_fingerprint.embedding_dim_from_collection(collection) if live_points else None
    add(
        "embedding_dim",
        stamped_dim is not None and (actual_dim is None or actual_dim == stamped_dim),
        f"stamped={stamped_dim} actual={actual_dim}",
    )

    consistency = check_index_consistency.check_collection(
        name, full=True, keyword_path=str(manifest.get("keyword_index_path") or "")
    )
    add(
        "index_consistency",
        consistency["consistent"],
        {k: consistency[k] for k in ("keyword_rows", "vector_rows", "missing_in_vector", "generation_mismatch", "vector_only")},
    )

    missing_tenant, offset, page = 0, 0, 1000
    while True:
        metas = collection.get(include=["metadatas"], limit=page, offset=offset).get("metadatas") or []
        missing_tenant += sum(1 for meta in metas if not str((meta or {}).get("tenant_id") or "").strip())
        offset += len(metas)
        if len(metas) < page:
            break
    add("tenant_id_present", missing_tenant == 0, f"{missing_tenant} rows without tenant_id")

    live = {"point_count": live_points, "keyword_rows": int(consistency["keyword_rows"])}
    status = str((manifest.get("verification") or {}).get("status") or "")
    if status == index_manifest.STATUS_STALE:
        add("counts_match_manifest", True, "skipped: the manifest is marked stale; counts are refreshed after a pass")
    else:
        add(
            "counts_match_manifest",
            live["point_count"] == manifest.get("point_count") and live["keyword_rows"] == manifest.get("keyword_rows"),
            f"live={live} manifest={{'point_count': {manifest.get('point_count')}, 'keyword_rows': {manifest.get('keyword_rows')}}}",
        )
    report = finish(live)
    if report["ok"] and update_manifest:
        manifest.update(point_count=live["point_count"], keyword_rows=live["keyword_rows"])
        manifest["verification"] = {"status": index_manifest.STATUS_VERIFIED, "checked_at": index_manifest.now_iso()}
        index_manifest.write_manifest(manifest)
    return report


def _failed(report: Dict[str, Any]) -> List[str]:
    return [c["name"] for c in report["checks"] if not c["ok"]]


def _entry(manifest: Dict[str, Any]) -> Dict[str, Any]:
    from rag_core import index_manifest

    return {
        "collection": manifest["collection"],
        "keyword_index_path": manifest["keyword_index_path"],
        "generation_id": manifest["generation_id"],
        "activated_at": index_manifest.now_iso(),
    }


# --- switching ------------------------------------------------------------------------------------


def activate(collection_name: str, *, provider_name: Optional[str] = None) -> Dict[str, Any]:
    from rag_core import index_manifest

    name = str(collection_name or "").strip()
    if _env_override():
        raise GenerationError(
            "CHROMA_COLLECTION is set; it overrides the active generation pointer, so activating would have no "
            "effect. Unset it first."
        )
    report = verify_generation(name, provider_name=provider_name, update_manifest=True)
    if not report["ok"]:
        raise GenerationError(f"generation '{name}' failed verification: {_failed(report)}", report)
    state = _raw_state()
    current = state.get("active")
    if current and current["collection"] == name:
        raise GenerationError(f"generation '{name}' is already active")
    history = list(state.get("history") or [])
    if current:
        history.append({**current, "deactivated_at": index_manifest.now_iso()})
    manifest = index_manifest.read_manifest(name)
    entry = _entry(manifest)
    _write_pointer({"schema_version": POINTER_SCHEMA, "active": entry, "history": history[-HISTORY_LIMIT:]})
    return {"active": entry, "previous": current, "verification": report}


def rollback(*, provider_name: Optional[str] = None) -> Dict[str, Any]:
    from rag_core import index_manifest

    if _env_override():
        raise GenerationError("CHROMA_COLLECTION is set; it overrides the active generation pointer.")
    state = _raw_state()
    history = list(state.get("history") or [])
    if not history:
        raise GenerationError("there is no previous generation to roll back to")
    name = str(history[-1]["collection"])
    report = verify_generation(name, provider_name=provider_name, update_manifest=True)
    if not report["ok"]:
        raise GenerationError(f"previous generation '{name}' failed verification: {_failed(report)}", report)
    current = state.get("active")
    entry = _entry(index_manifest.read_manifest(name))
    entry["rolled_back_from"] = current["collection"] if current else None
    _write_pointer({"schema_version": POINTER_SCHEMA, "active": entry, "history": history[:-1]})
    return {"active": entry, "rolled_back_from": current, "verification": report}


def clear() -> Dict[str, Any]:
    """Stop serving a generation: back to the environment and CHUNKS_JSONL_PATH (the history is kept)."""
    state = _raw_state()
    _write_pointer({"schema_version": POINTER_SCHEMA, "active": None, "history": list(state.get("history") or [])})
    return {"active": None}


def status() -> Dict[str, Any]:
    state = _raw_state()
    return {
        "pointer_file": str(pointer_path()),
        "env_override": _env_override(),
        "active": state.get("active"),
        "history": state.get("history") or [],
    }
