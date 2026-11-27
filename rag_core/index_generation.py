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
import logging
import os
import threading
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import config

logger = logging.getLogger(__name__)

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


def fsync_dir(directory: Path) -> None:
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write_text(path: Path, text: str) -> None:
    """Write to a unique temp name, fsync it, rename over `path`, fsync the directory (survives a power cut)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)  # already gone after a successful rename
    fsync_dir(path.parent)


_LOCK_WAIT_SECONDS = 30.0


class LockTimeout(RuntimeError):
    """An exclusive file lock was not obtained in time (a request must not hang a worker forever)."""


def flock_exclusive(handle, wait_seconds: Optional[float] = None, *, what: str = "lock") -> None:
    """flock(LOCK_EX) polled for at most wait_seconds (default 30): LockTimeout instead of blocking forever.
    Release with fcntl.flock(handle, fcntl.LOCK_UN) (or by closing the handle)."""
    import fcntl
    import time

    limit = _LOCK_WAIT_SECONDS if wait_seconds is None else max(0.0, float(wait_seconds))
    deadline = time.monotonic() + limit
    while True:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise LockTimeout(f"{what} is held by another process; retry later") from None
            time.sleep(0.1)


@contextmanager
def _pointer_lock() -> Iterator[None]:
    """Read-modify-write of the pointer file by one process at a time (activate / rollback / clear)."""
    import fcntl

    path = pointer_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(f".{path.name}.lock"), "a+") as handle:
        flock_exclusive(handle, what="the generation pointer")
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _write_pointer(data: Dict[str, Any]) -> None:
    atomic_write_text(pointer_path(), json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
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

    from rag_core.mail_access import public_vector_row, vector_columns

    scanned_ids: set[str] = set()
    aligned = True
    missing_tenant, unsafe_rows, offset, page = 0, 0, 0, 1000
    while True:
        res = collection.get(include=["metadatas"], limit=page, offset=offset)
        columns = vector_columns(res, "metadatas")
        if columns is None:
            aligned = False
            break
        ids, metas = columns
        if not scanned_ids.isdisjoint(ids):
            aligned = False
            break
        scanned_ids.update(ids)
        missing_tenant += sum(1 for meta in metas if not isinstance(meta, dict) or not str(meta.get("tenant_id") or "").strip())
        unsafe_rows += sum(not public_vector_row(row_id, meta) for row_id, meta in zip(ids, metas))
        offset += len(metas)
        if len(metas) < page or offset > live_points:
            break
    add("tenant_id_present", missing_tenant == 0, f"{missing_tenant} rows without tenant_id")
    keyword_mail = keyword_mail_rows(str(manifest.get("keyword_index_path") or ""))
    # Private mail is quarantined: a generation holding any mail row is never served (fail-closed).
    add("no_private_mail", aligned and offset == live_points and unsafe_rows == 0 and keyword_mail == 0,
        f"{unsafe_rows} unsafe vector row(s), {keyword_mail} private keyword row(s), scanned {offset}/{live_points}, aligned={aligned}")

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


def keyword_mail_rows(path: str) -> int:
    from rag_core.mail_access import is_private_mail_record

    count = 0
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and is_private_mail_record(row):
                    count += 1
    except (FileNotFoundError, IsADirectoryError):
        return 0
    return count


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


TOMBSTONE_MODES = ("purge", "refuse", "ignore")


def _refuse_writing(name: str) -> None:
    """A collection that an ingestion job is (or was, before a crash) writing is never served."""
    try:
        from webapi import ingestion_jobs
    except ImportError:  # no web layer (CLI-only install): nothing registered is being written
        return
    try:
        entry = ingestion_jobs.get_collection(name)
    except Exception as exc:  # noqa: BLE001 - fail closed: an unreadable registry cannot prove the generation is complete
        raise GenerationError(
            f"generation '{name}' cannot be served: the ingestion registry is unreadable ({type(exc).__name__})"
        ) from exc
    if entry and entry.get("status") == ingestion_jobs.COLLECTION_WRITING:
        raise GenerationError(
            f"generation '{name}' is being written by an ingestion job (or one was interrupted): it cannot be served"
        )


@contextmanager
def _switch_locks(target: str) -> Iterator[None]:
    """The target's write lock, then the served collection's, then the pointer lock.

    Promotion and deletion hold the served collection's lock while they write and check the served name after
    taking it, so a switch never happens in the middle of their writes (same order: non-served first)."""
    from webapi import ingestion_jobs

    with ExitStack() as stack:
        stack.enter_context(ingestion_jobs._collection_write_lock(target))
        served = str(config.resolve_chroma_collection_name() or "").strip()
        if served and served != target:
            stack.enter_context(ingestion_jobs._collection_write_lock(served))
        stack.enter_context(_pointer_lock())
        yield


def _tombstones(name: str, mode: str) -> Dict[str, Any]:
    """Documents deleted since this generation was built must not come back when it is served again."""
    if mode not in TOMBSTONE_MODES:
        raise GenerationError(f"tombstones must be one of {TOMBSTONE_MODES}")
    if mode == "ignore":
        from webapi import production_guard

        if production_guard.is_production():
            raise GenerationError("tombstones='ignore' is not allowed in production")
        return {"mode": mode}
    from rag_core import index_manifest

    try:
        manifest = index_manifest.read_manifest(name)
    except ValueError:
        return {"mode": mode}
    if manifest is None:
        return {"mode": mode}  # verification reports the missing manifest
    keyword_path = str(manifest.get("keyword_index_path") or "")
    from webapi import collection_admin, ingestion_jobs

    if mode == "refuse":
        keys = sorted(ingestion_jobs.tombstoned_keys())
        if not keys:
            return {"mode": mode, "documents": 0}
        from rag_core import store

        vectors = store.get_vectorstore(collection_name=name, create_if_missing=False, verify_embedding_fingerprint=False)
        present = collection_admin._present_tombstones(vectors, keyword_path, keys)
        if present:
            raise GenerationError(
                f"generation '{name}' still holds {len(present)} deleted (tombstoned) document(s); activate with "
                "tombstones='purge' to remove them first"
            )
        # A clean index does not prove that the history visibility boundary was established.
        try:
            for tenant_id in sorted({t for t, _ in keys}):
                collection_admin._redact_conversations(tenant_id, [doc for t, doc in keys if t == tenant_id])
        except collection_admin.ConversationRedactionError as exc:
            raise GenerationError("conversation redaction failed; generation switch refused") from exc
        return {"mode": mode, "documents": 0}
    try:
        return {"mode": mode, **collection_admin.purge_tombstoned(name, keyword_path)}
    except Exception as exc:  # noqa: BLE001  re-raised as GenerationError with the cause
        raise GenerationError(f"could not remove deleted documents from '{name}': {type(exc).__name__}: {exc}") from exc


def activate(collection_name: str, *, provider_name: Optional[str] = None, tombstones: str = "purge") -> Dict[str, Any]:
    """Verify a generation and serve it. tombstones: "purge" (default) removes deleted documents from it first,
    "refuse" fails when it holds any, "ignore" skips the check (non-production only)."""
    from rag_core import index_manifest

    name = str(collection_name or "").strip()
    if _env_override():
        raise GenerationError(
            "CHROMA_COLLECTION is set; it overrides the active generation pointer, so activating would have no "
            "effect. Unset it first."
        )
    _refuse_writing(name)
    with _switch_locks(name):
        state = _raw_state()
        current = state.get("active")
        if current and current["collection"] == name:
            raise GenerationError(f"generation '{name}' is already active")
        purged = _tombstones(name, tombstones)
        report = verify_generation(name, provider_name=provider_name, update_manifest=True)
        if not report["ok"]:
            raise GenerationError(f"generation '{name}' failed verification: {_failed(report)}", report)
        history = list(state.get("history") or [])
        if current:
            history.append({**current, "deactivated_at": index_manifest.now_iso()})
        manifest = index_manifest.read_manifest(name)
        entry = _entry(manifest)
        _write_pointer({"schema_version": POINTER_SCHEMA, "active": entry, "history": history[-HISTORY_LIMIT:]})
    _flush_answer_cache()
    return {"active": entry, "previous": current, "verification": report, "tombstones": purged}


def rollback(*, provider_name: Optional[str] = None, tombstones: str = "purge") -> Dict[str, Any]:
    from rag_core import index_manifest

    if _env_override():
        raise GenerationError("CHROMA_COLLECTION is set; it overrides the active generation pointer.")
    history = list(_raw_state().get("history") or [])
    if not history:
        raise GenerationError("there is no previous generation to roll back to")
    name = str(history[-1]["collection"])
    _refuse_writing(name)
    with _switch_locks(name):
        state = _raw_state()
        history = list(state.get("history") or [])
        if not history or str(history[-1]["collection"]) != name:
            raise GenerationError("the generation history changed meanwhile; retry")
        purged = _tombstones(name, tombstones)
        report = verify_generation(name, provider_name=provider_name, update_manifest=True)
        if not report["ok"]:
            raise GenerationError(f"previous generation '{name}' failed verification: {_failed(report)}", report)
        current = state.get("active")
        entry = _entry(index_manifest.read_manifest(name))
        entry["rolled_back_from"] = current["collection"] if current else None
        _write_pointer({"schema_version": POINTER_SCHEMA, "active": entry, "history": history[:-1]})
    _flush_answer_cache()
    return {"active": entry, "rolled_back_from": current, "verification": report, "tombstones": purged}


def _flush_answer_cache() -> None:
    """Answers cached from the previous generation must not be served from the new one."""
    try:
        from rag_core import answer_cache

        answer_cache.clear()
    except Exception:  # noqa: BLE001 - the switch succeeded; cached entries expire with their TTL
        logger.warning("answer cache could not be flushed after a generation switch", exc_info=True)


def clear() -> Dict[str, Any]:
    """Stop serving a generation: back to the environment and CHUNKS_JSONL_PATH (the history is kept)."""
    with _pointer_lock():
        state = _raw_state()
        _write_pointer({"schema_version": POINTER_SCHEMA, "active": None, "history": list(state.get("history") or [])})
    _flush_answer_cache()
    return {"active": None}


def status() -> Dict[str, Any]:
    state = _raw_state()
    return {
        "pointer_file": str(pointer_path()),
        "env_override": _env_override(),
        "active": state.get("active"),
        "history": state.get("history") or [],
    }


# --- health (readiness, monitoring, release check) -------------------------------------------------


def pointer_status() -> Dict[str, Any]:
    """{"ok", "detail"}: False when a pointer file exists but cannot be read (queries fail closed then)."""
    try:
        _load()
    except PointerError as exc:
        return {"ok": False, "detail": str(exc)}
    return {"ok": True, "detail": ""}


def fingerprint_status(provider_name: Optional[str] = None) -> Dict[str, Any]:
    """The running embedding profile against the manifest of the served generation.

    {"ok", "collection", "differs", "manifest_status", "manifest", "detail"}. ok is None when the served collection
    has no manifest (nothing to compare: legacy install), False when the profile differs or the manifest is
    unreadable."""
    from rag_core import embedding_fingerprint, index_manifest

    try:
        pointer = active_pointer()
    except PointerError as exc:
        return {"ok": False, "collection": None, "differs": [], "manifest_status": None, "manifest": False,
                "detail": f"pointer: {exc}"}
    name = str((pointer or {}).get("collection") or config.resolve_chroma_collection_name() or "")
    try:
        manifest = index_manifest.read_manifest(name)
    except ValueError as exc:
        return {"ok": False, "collection": name, "differs": [], "manifest_status": None, "manifest": False,
                "detail": str(exc)}
    if manifest is None:
        return {"ok": None, "collection": name, "differs": [], "manifest_status": None, "manifest": False,
                "detail": "the served collection has no manifest (scripts/switch_generation.py register)"}
    runtime = embedding_fingerprint.build_collection_fingerprint_metadata(provider_name=provider_name)
    declared = manifest.get("profile") or {}
    differs = [k for k in index_manifest.PROFILE_KEYS
               if k != "embedding_dim" and str(declared.get(k)) != str(runtime.get(k))]
    return {
        "ok": not differs, "collection": name, "differs": differs, "manifest": True,
        "manifest_status": str((manifest.get("verification") or {}).get("status") or ""),
        "point_count": manifest.get("point_count"), "keyword_rows": manifest.get("keyword_rows"),
        "detail": "re-embed into a new generation, verify and activate it" if differs else "",
    }
