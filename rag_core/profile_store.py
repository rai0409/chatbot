"""Versions of a RAG profile.

A version is an immutable copy of the profile files (the JSON files of profile_loader.JSON_FILES and golden_qa.jsonl)
named by a digest of their normalized content: the same content is the same version however it is formatted, a
changed value is a new version, and a stored version that no longer matches its digest is refused.

Versions live under PROFILE_STORE_DIR (default data/profile_versions)/<profile id>/versions/<16 hex>/, and
<profile id>/active.json says which one is served. Without that pointer, or after clear(), the profile is read from
config/rag_profiles/<id>/exported as before. activate() and rollback() write the pointer only after the version has
been verified; neither touches any version. An unreadable pointer is an error (the loader answers with an empty
profile and a warning), never a silent fall back to another version.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import config

POINTER_SCHEMA = 1
VERSION_SCHEMA = 1
HISTORY_LIMIT = 20
GOLDEN_FILE = "golden_qa.jsonl"
SOURCE = "source"  # diff_versions(): the files under config/rag_profiles/<id>/exported

_VERSION_RE = re.compile(r"^[0-9a-f]{16}$")
_OVERRIDES: Dict[str, str] = {}  # profile id -> version served in this process only (override_version)


class ProfileStoreError(RuntimeError):
    """The request is refused or the store cannot be used. Messages carry no server paths."""


def _loader():
    from rag_core import profile_loader

    return profile_loader


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def store_root() -> Path:
    raw = str(os.getenv("PROFILE_STORE_DIR", "")).strip()
    return Path(raw) if raw else Path(config.BASE_DIR) / "data" / "profile_versions"


def _check_profile_id(profile_id: Any) -> str:
    if not _loader().is_valid_rag_profile_id(profile_id):
        raise ProfileStoreError("invalid rag profile id")
    return profile_id


def _check_version(version: Any) -> str:
    if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
        raise ProfileStoreError("invalid profile version (expected 16 lowercase hex digits)")
    return version


def profile_store_dir(profile_id: str) -> Path:
    return store_root() / _check_profile_id(profile_id)


def version_dir(profile_id: str, version: str) -> Path:
    return profile_store_dir(profile_id) / "versions" / _check_version(version)


def pointer_path(profile_id: str) -> Path:
    return profile_store_dir(profile_id) / "active.json"


def source_dir(profile_id: str) -> Path:
    return _loader().PROFILE_ROOT / _check_profile_id(profile_id) / "exported"


def _filenames() -> List[str]:
    return sorted([name for name, _root in _loader().JSON_FILES.values()] + [GOLDEN_FILE])


# --- content digest -------------------------------------------------------------------------------


def _canonical(name: str, raw: bytes) -> str:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProfileStoreError(f"{name}: not valid UTF-8") from exc
    dump = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))  # noqa: E731
    if name.endswith(".jsonl"):
        rows = []
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                rows.append(dump(json.loads(line)))
            except ValueError as exc:
                raise ProfileStoreError(f"{name}: line {number}: invalid JSON") from exc
        return "\n".join(rows)
    try:
        return dump(json.loads(text))
    except ValueError as exc:
        raise ProfileStoreError(f"{name}: invalid JSON") from exc


def content_digest(directory: Path) -> str:
    """SHA-256 of the normalized profile files in a directory (key order, indentation and blank lines do not count)."""
    digest = hashlib.sha256()
    for name in _filenames():
        try:
            raw = (Path(directory) / name).read_bytes()
        except FileNotFoundError:
            digest.update(f"{name}\0absent\n".encode("utf-8"))
            continue
        except OSError as exc:
            raise ProfileStoreError(f"{name}: read failed: {type(exc).__name__}") from exc
        digest.update(f"{name}\0".encode("utf-8"))
        digest.update(_canonical(name, raw).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


# --- versions -------------------------------------------------------------------------------------


def read_version(profile_id: str, version: str) -> Dict[str, Any]:
    path = version_dir(profile_id, version) / "version.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProfileStoreError(f"profile version {version} not found") from exc
    except (OSError, ValueError) as exc:
        raise ProfileStoreError(f"profile version {version}: version.json is unreadable") from exc
    if not isinstance(data, dict) or data.get("schema_version") != VERSION_SCHEMA:
        raise ProfileStoreError(f"profile version {version}: unsupported version.json")
    if not str(data.get("sha256") or "").startswith(version):
        raise ProfileStoreError(f"profile version {version}: version.json does not match the version")
    return data


def verify_version(profile_id: str, version: str) -> Dict[str, Any]:
    """Raise unless the stored files still hash to the version's digest and nothing was added or removed."""
    meta = read_version(profile_id, version)
    directory = version_dir(profile_id, version)
    if content_digest(directory) != meta["sha256"]:
        raise ProfileStoreError(f"profile version {version}: stored files do not match the digest")
    present = sorted(name for name in _filenames() if (directory / name).is_file())
    if present != sorted(meta.get("files") or []):
        raise ProfileStoreError(f"profile version {version}: stored files do not match the file list")
    return meta


def list_versions(profile_id: str) -> List[Dict[str, Any]]:
    root = profile_store_dir(profile_id) / "versions"
    if not root.is_dir():
        return []
    rows: List[Dict[str, Any]] = []
    for entry in root.iterdir():
        if not _VERSION_RE.fullmatch(entry.name):
            continue
        try:
            rows.append(read_version(profile_id, entry.name))
        except ProfileStoreError as exc:
            rows.append({"version": entry.name, "error": str(exc)})
    return sorted(rows, key=lambda row: (str(row.get("created_at") or ""), str(row.get("version"))))


def create_version(
    profile_id: str, *, source: Optional[Path] = None, created_by: str = "", note: str = ""
) -> Dict[str, Any]:
    """Store the profile files of `source` (default: config/rag_profiles/<id>/exported) as a version.

    The same content gives the same version (created: False, nothing is written). A source file that is not valid
    JSON refuses the version. The version is written to a temporary directory and moved into place whole."""
    src = Path(source) if source else source_dir(profile_id)
    if not src.is_dir():
        raise ProfileStoreError("profile source directory not found")
    present = [name for name in _filenames() if (src / name).is_file()]
    if not present:
        raise ProfileStoreError("profile source has none of the profile files")
    digest = content_digest(src)
    version = digest[:16]
    final = version_dir(profile_id, version)
    if final.is_dir():
        return {**read_version(profile_id, version), "created": False}
    final.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".tmp-", dir=final.parent))
    try:
        for name in present:
            shutil.copyfile(src / name, staging / name)
        if content_digest(staging) != digest:
            raise ProfileStoreError("the profile source changed while it was being copied; try again")
        meta = {
            "schema_version": VERSION_SCHEMA,
            "version": version,
            "sha256": digest,
            "files": present,
            "created_at": now_iso(),
            "created_by": str(created_by or ""),
            "note": str(note or ""),
            "parent": pinned_version(profile_id),
        }
        (staging / "version.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        try:
            os.replace(staging, final)
        except OSError:
            if not final.is_dir():
                raise
            return {**read_version(profile_id, version), "created": False}  # a concurrent create won
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return {**meta, "created": True}


def diff_versions(profile_id: str, left: str, right: str) -> Dict[str, Any]:
    """Per file: same / changed / added / removed. Either side may be SOURCE (the files under config/)."""

    def directory(ref: str) -> Path:
        return source_dir(profile_id) if ref == SOURCE else version_dir(profile_id, ref)

    def canonical(path: Path) -> Optional[str]:
        try:
            return _canonical(path.name, path.read_bytes())
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ProfileStoreError(f"{path.name}: read failed: {type(exc).__name__}") from exc

    left_dir, right_dir = directory(left), directory(right)
    files: Dict[str, str] = {}
    for name in _filenames():
        a, b = canonical(left_dir / name), canonical(right_dir / name)
        files[name] = "same" if a == b else "added" if a is None else "removed" if b is None else "changed"
    return {"left": left, "right": right, "files": files, "identical": all(v == "same" for v in files.values())}


# --- the active pointer ---------------------------------------------------------------------------


def _read_pointer(profile_id: str) -> Dict[str, Any]:
    path = pointer_path(profile_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema_version": POINTER_SCHEMA, "active": None, "history": []}
    except (OSError, ValueError) as exc:
        raise ProfileStoreError("active profile pointer is unreadable") from exc
    active = data.get("active") if isinstance(data, dict) else None
    valid = (
        isinstance(data, dict)
        and data.get("schema_version") == POINTER_SCHEMA
        and (active is None or (isinstance(active, dict) and _VERSION_RE.fullmatch(str(active.get("version") or ""))))
        and isinstance(data.get("history", []), list)
    )
    if not valid:
        raise ProfileStoreError("active profile pointer has an unsupported format")
    return data


def _write_pointer(profile_id: str, data: Dict[str, Any]) -> None:
    path = pointer_path(profile_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


@contextlib.contextmanager
def override_version(profile_id: str, version: str) -> Iterator[None]:
    """Serve a stored version of the profile in this process only, until the block ends.

    For evaluating a candidate without activating it: the pointer file is not touched, other processes are not
    affected, and the answer-cache key follows the version being served."""
    _check_profile_id(profile_id)
    verify_version(profile_id, version)
    missing = object()
    previous = _OVERRIDES.get(profile_id, missing)
    _OVERRIDES[profile_id] = version
    try:
        yield
    finally:
        if previous is missing:
            _OVERRIDES.pop(profile_id, None)
        else:
            _OVERRIDES[profile_id] = previous  # type: ignore[assignment]


def pinned_version(profile_id: str) -> Optional[str]:
    if _check_profile_id(profile_id) in _OVERRIDES:
        return _OVERRIDES[profile_id]
    active = _read_pointer(profile_id).get("active")
    return str(active["version"]) if active else None


def pinned_dir(profile_id: str) -> Optional[Path]:
    """The directory of the active version, or None when the profile is read from config/."""
    version = pinned_version(profile_id)
    if version is None:
        return None
    directory = version_dir(profile_id, version)
    if not directory.is_dir():
        raise ProfileStoreError(f"active profile version {version} is missing from the store")
    return directory


def _state(active: Optional[Dict[str, Any]], history: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"schema_version": POINTER_SCHEMA, "active": active, "history": history[-HISTORY_LIMIT:]}


def activate(profile_id: str, version: str, *, activated_by: str = "") -> Dict[str, Any]:
    verify_version(profile_id, version)
    state = _read_pointer(profile_id)
    current = state.get("active")
    if current and current["version"] == version:
        raise ProfileStoreError(f"profile version {version} is already active")
    history = list(state.get("history") or [])
    if current:
        history.append({**current, "deactivated_at": now_iso()})
    entry = {"version": version, "activated_at": now_iso(), "activated_by": str(activated_by or "")}
    _write_pointer(profile_id, _state(entry, history))
    return {"active": entry, "previous": current}


def rollback(profile_id: str, *, activated_by: str = "") -> Dict[str, Any]:
    state = _read_pointer(profile_id)
    history = list(state.get("history") or [])
    if not history:
        raise ProfileStoreError("there is no previous profile version to roll back to")
    version = str(history[-1]["version"])
    _check_version(version)
    verify_version(profile_id, version)
    current = state.get("active")
    entry = {
        "version": version,
        "activated_at": now_iso(),
        "activated_by": str(activated_by or ""),
        "rolled_back_from": current["version"] if current else None,
    }
    _write_pointer(profile_id, _state(entry, history[:-1]))
    return {"active": entry, "rolled_back_from": current}


def clear(profile_id: str) -> Dict[str, Any]:
    """Read the profile from config/ again. The cleared version goes to the history, so rollback restores it."""
    state = _read_pointer(profile_id)
    current = state.get("active")
    history = list(state.get("history") or [])
    if current:
        history.append({**current, "deactivated_at": now_iso()})
    _write_pointer(profile_id, _state(None, history))
    return {"active": None, "cleared": current}


def status(profile_id: str) -> Dict[str, Any]:
    state = _read_pointer(profile_id)
    return {
        "profile_id": profile_id,
        "pointer_file": str(pointer_path(profile_id)),
        "active": state.get("active"),
        "history": state.get("history") or [],
        "versions": [row.get("version") for row in list_versions(profile_id)],
    }
