"""Registered document sources and their manifests (step 5-1)."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import config

SCHEMA = 1
KINDS = ("folder", "sharepoint")
MAX_SCHEDULE_MINUTES = 7 * 24 * 60
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
_TENANT_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_COLLECTION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class SourceError(ValueError):
    """The request is refused or the registry cannot be used. Messages carry no server paths."""


class SourceUnavailable(SourceError):
    """The source cannot be read now (folder missing or not mounted): not the same as a source with no files."""


def sources_dir() -> Path:
    raw = str(os.getenv("SOURCES_DIR", "")).strip()
    return Path(raw) if raw else Path(config.BASE_DIR) / "data" / "sources"


def mirror_dir(source_id: str) -> Path:
    return sources_dir() / "mirror" / source_id


def allowed_roots() -> List[Path]:
    """INGEST_ALLOWED_ROOTS, resolved (the same rule as the ingestion jobs): every source path is confined to them."""
    roots: List[Path] = []
    for part in str(os.getenv("INGEST_ALLOWED_ROOTS", "")).split(os.pathsep):
        part = part.strip()
        if part:
            root = Path(part)
            roots.append((root if root.is_absolute() else Path(config.BASE_DIR) / root).resolve())
    return roots


def _inside_allowed_roots(path: Path) -> bool:
    resolved = path.resolve()
    return any(resolved == root or resolved.is_relative_to(root) for root in allowed_roots())


def _now(now: Optional[datetime]) -> str:
    return (now or datetime.now(timezone.utc)).isoformat()


def _check_id(source_id: Any) -> str:
    if not isinstance(source_id, str) or not _ID_RE.fullmatch(source_id):
        raise SourceError("invalid source id (lower-case letters, digits, '_' and '-', at most 48)")
    return source_id


# --- storage --------------------------------------------------------------------------------------


def _registry_file() -> Path:
    return sources_dir() / "registry.json"


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _load() -> Dict[str, Any]:
    try:
        data = json.loads(_registry_file().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema": SCHEMA, "rev": 0, "sources": {}}
    except (OSError, ValueError) as exc:
        raise SourceError("the source registry is unreadable") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or not isinstance(data.get("sources"), dict):
        raise SourceError("the source registry has an unsupported format")
    return data


def _save(data: Dict[str, Any]) -> None:
    current = _load()
    if current["rev"] != data["rev"]:
        raise SourceError("the source registry was changed by someone else; try again")
    data["rev"] += 1
    _write_json(_registry_file(), data)


def get(source_id: str) -> Dict[str, Any]:
    record = _load()["sources"].get(_check_id(source_id))
    if record is None:
        raise SourceError(f"source '{source_id}' not found")
    return record


def list_sources(tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
    rows = [record for _id, record in sorted(_load()["sources"].items())]
    return [r for r in rows if tenant_id is None or r["tenant_id"] == tenant_id]


def create(
    source_id: str, *, tenant_id: str, kind: str = "folder", path: Optional[str] = None,
    options: Optional[Mapping[str, Any]] = None, schedule_minutes: int = 0, collection: Optional[str] = None,
    created_by: str = "", now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Register a source. A folder's path must be a real directory (not a symlink) inside INGEST_ALLOWED_ROOTS."""
    _check_id(source_id)
    if not isinstance(tenant_id, str) or not _TENANT_RE.fullmatch(tenant_id):
        raise SourceError("invalid tenant id")
    if kind not in KINDS:
        raise SourceError(f"unknown kind '{kind}' (one of: {', '.join(KINDS)})")
    if isinstance(schedule_minutes, bool) or not isinstance(schedule_minutes, int) or not 0 <= schedule_minutes <= MAX_SCHEDULE_MINUTES:
        raise SourceError(f"schedule_minutes must be an integer from 0 (manual) to {MAX_SCHEDULE_MINUTES}")
    name = collection or f"src-{source_id}"
    if not _COLLECTION_RE.fullmatch(name):
        raise SourceError("invalid collection name")
    if name == config.resolve_chroma_collection_name():
        raise SourceError("a source writes to a staging collection, never the served collection")
    stored_path: Optional[str] = None
    if kind == "folder":
        if not path:
            raise SourceError("a folder source needs a path")
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = Path(config.BASE_DIR) / candidate
        if candidate.is_symlink():
            raise SourceError("a symlinked folder is refused")
        if not allowed_roots():
            raise SourceError("ingest roots are not configured (INGEST_ALLOWED_ROOTS)")
        if not _inside_allowed_roots(candidate):
            raise SourceError("the folder is outside the allowed ingest roots")
        if not candidate.is_dir():
            raise SourceError("the folder does not exist")
        stored_path = str(candidate.resolve())
    else:
        opts = options or {}
        for key in ("drive_id", "folder"):
            if not str(opts.get(key) or "").strip():
                raise SourceError(f"a sharepoint source needs options.{key}")
        if not str(opts["folder"]).startswith("/"):
            raise SourceError("options.folder must start with '/', e.g. /Shared Documents/Policies")
        if not _inside_allowed_roots(mirror_dir(source_id)):
            raise SourceError("the mirror folder of a remote source must be inside the allowed ingest roots "
                              "(put SOURCES_DIR under INGEST_ALLOWED_ROOTS)")
    data = _load()
    if source_id in data["sources"]:
        raise SourceError(f"source '{source_id}' already exists")
    record = {
        "source_id": source_id, "tenant_id": tenant_id, "kind": kind, "path": stored_path,
        "options": dict(options or {}), "schedule_minutes": schedule_minutes, "collection": name, "enabled": True,
        "created_at": _now(now), "created_by": str(created_by or ""),
        "state": {"last_scan_at": None, "last_scan_run": None, "last_apply_at": None, "last_error": None},
    }
    data["sources"][source_id] = record
    _save(data)
    return record


def _update(source_id: str, change) -> Dict[str, Any]:
    data = _load()
    record = data["sources"].get(_check_id(source_id))
    if record is None:
        raise SourceError(f"source '{source_id}' not found")
    change(record)
    _save(data)
    return record


def set_enabled(source_id: str, enabled: bool) -> Dict[str, Any]:
    return _update(source_id, lambda record: record.__setitem__("enabled", bool(enabled)))


def update_state(source_id: str, **fields: Any) -> Dict[str, Any]:
    unknown = set(fields) - {"last_scan_at", "last_scan_run", "last_apply_at", "last_error"}
    if unknown:
        raise SourceError("unknown state field: " + ", ".join(sorted(unknown)))
    return _update(source_id, lambda record: record["state"].update(fields))


def remove(source_id: str) -> None:
    """Unregister a source. Its collection, manifests and ledger entries are left as they are."""
    data = _load()
    if data["sources"].pop(_check_id(source_id), None) is None:
        raise SourceError(f"source '{source_id}' not found")
    _save(data)


def root_of(record: Mapping[str, Any]) -> Path:
    """The folder the scanner reads: the folder itself, or the local mirror of a remote source."""
    return Path(record["path"]) if record["kind"] == "folder" else mirror_dir(record["source_id"])


# --- manifests ------------------------------------------------------------------------------------


def _manifest_file(source_id: str, which: str) -> Path:
    if which not in {"applied", "observed"}:
        raise SourceError("unknown manifest")
    return sources_dir() / "manifests" / f"{_check_id(source_id)}.{which}.json"


def load_manifest(source_id: str, which: str) -> Dict[str, Dict[str, Any]]:
    path = _manifest_file(source_id, which)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise SourceError(f"the {which} manifest of '{source_id}' is unreadable") from exc
    return data if isinstance(data, dict) else {}


def save_manifest(source_id: str, which: str, manifest: Mapping[str, Any]) -> None:
    _write_json(_manifest_file(source_id, which), dict(manifest))
