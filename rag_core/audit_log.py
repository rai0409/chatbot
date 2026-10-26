from __future__ import annotations

"""Append-only audit log (JSONL) with actor, hash chain and rotation (6-4).

Every record carries:
  actor       who made the request: key fingerprint, role, authenticated
              (set per request with set_audit_actor; never the raw key)
  prev_hash   hash of the previous record in the same file family
  hash        sha256(prev_hash + canonical JSON of the record)
so deleting or editing a line breaks the chain
(scripts/verify_audit_chain.py). Files rotate at AUDIT_MAX_BYTES (default
50 MiB) to <name>.<UTC timestamp>.jsonl; the chain continues across files.

Question text: AUDIT_QUESTION_MODE = plain | mask (digits and e-mail
addresses masked) | hash (sha256 only) | drop. Unset: mask in production
(APP_ENV=production), plain otherwise (stage 14). Free text written by users
(search queries, feedback comments, review notes, rewritten questions) is
treated like the question.
Write failures are counted (write_failure_count) for metrics and alerts.
"""

import hashlib
import json
import os
import re
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional

import config


_MAX_STRING_CHARS = 1000
_MAX_LIST_ITEMS = 20
_MAX_DICT_ITEMS = 40
_DEFAULT_MAX_BYTES = 50 * 1024 * 1024
_QUESTION_FIELDS = (
    "question", "user_query", "normalized_query", "normalized_question", "original_query",
    # Stage 14: other user-written text that used to stay in plain text.
    "query", "comment", "operator_note", "contextualized_question", "rewritten_query", "augmented_query",
)
# Nested dicts whose user text is protected the same way.
_NESTED_FIELDS = ("conversation_context",)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_DIGITS_RE = re.compile(r"\d")
_GENESIS = "0" * 64

_ACTOR: ContextVar[Optional[Dict[str, Any]]] = ContextVar("audit_actor", default=None)
_LOCK = Lock()
# path -> (hash of the last record we wrote, file size right after it)
_LAST_HASH: Dict[str, tuple] = {}
_FAILURES = {"count": 0}


def set_audit_actor(auth: Any) -> None:
    """Record the caller of the current request (duck-typed ApiAuthContext)."""
    if auth is None or not hasattr(auth, "authenticated"):
        _ACTOR.set({"authenticated": False, "key_fingerprint": None, "role": None})
        return
    _ACTOR.set(
        {
            "authenticated": bool(getattr(auth, "authenticated", False)),
            "key_fingerprint": getattr(auth, "key_fingerprint", None),
            "role": getattr(auth, "role", None),
        }
    )


def write_failure_count() -> int:
    return int(_FAILURES["count"])


def _audit_path(kind: str) -> Path | None:
    if kind == "chat":
        if not getattr(config, "AUDIT_CHAT_ENABLED", True):
            return None
        return Path(config.RUNS_DIR) / "audit" / "chat_events.jsonl"
    if kind == "search_debug":
        if not getattr(config, "AUDIT_SEARCH_DEBUG_ENABLED", True):
            return None
        return Path(config.RUNS_DIR) / "audit" / "search_debug_events.jsonl"
    return Path(config.RUNS_DIR) / "audit" / "events.jsonl"


def _bounded(value: Any) -> Any:
    if isinstance(value, str):
        if len(value) > _MAX_STRING_CHARS:
            return value[:_MAX_STRING_CHARS] + "...[truncated]"
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_bounded(item) for item in value[:_MAX_LIST_ITEMS]]
    if isinstance(value, tuple):
        return [_bounded(item) for item in list(value)[:_MAX_LIST_ITEMS]]
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= _MAX_DICT_ITEMS:
                break
            out[str(key)] = _bounded(item)
        return out
    return str(value)


def _question_mode() -> str:
    raw = str(os.getenv("AUDIT_QUESTION_MODE", "")).strip().lower()
    if not raw:
        return "mask" if str(os.getenv("APP_ENV", "")).strip().lower() == "production" else "plain"
    return raw if raw in {"plain", "mask", "hash", "drop"} else "mask"


def _protect_questions(payload: Dict[str, Any]) -> None:
    mode = _question_mode()
    if mode == "plain":
        return
    for key in _NESTED_FIELDS:
        nested = payload.get(key)
        if isinstance(nested, dict):
            nested = dict(nested)
            _protect_fields(nested, mode, tuple(nested))
            payload[key] = nested
    _protect_fields(payload, mode, _QUESTION_FIELDS)


def _protect_fields(payload: Dict[str, Any], mode: str, keys) -> None:
    for key in keys:
        value = payload.get(key)
        if not isinstance(value, str):
            continue
        if mode == "drop":
            payload.pop(key)
        elif mode == "hash":
            payload[key] = "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()
        else:
            payload[key] = _DIGITS_RE.sub("#", _EMAIL_RE.sub("[email]", value))


def _canonical(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def _record_hash(prev_hash: str, payload: Dict[str, Any]) -> str:
    return hashlib.sha256((prev_hash + _canonical(payload)).encode("utf-8")).hexdigest()


def _tail_hash(path: Path) -> str:
    # Hash of the last record of the newest file of this family (current file
    # or, right after a rotation, the newest rotated file).
    candidates = [path] if path.exists() and path.stat().st_size > 0 else []
    if not candidates:
        rotated = sorted(path.parent.glob(f"{path.stem}.*{path.suffix}"))
        candidates = rotated[-1:]
    for candidate in candidates:
        with candidate.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 65536))
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
        for raw in reversed(lines):
            try:
                value = json.loads(raw.decode("utf-8")).get("hash")
            except (ValueError, UnicodeDecodeError, AttributeError):
                continue
            if value:
                return str(value)
    return _GENESIS


def _max_bytes() -> int:
    try:
        return int(str(os.getenv("AUDIT_MAX_BYTES", "")).strip() or _DEFAULT_MAX_BYTES)
    except ValueError:
        return _DEFAULT_MAX_BYTES


def _rotate_if_needed(path: Path) -> None:
    if path.exists() and path.stat().st_size >= _max_bytes():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path.rename(path.with_name(f"{path.stem}.{stamp}{path.suffix}"))


class _FileLock:
    """Exclusive lock shared by every process writing this audit file
    (several uvicorn workers); POSIX flock, a no-op where it is missing."""

    def __init__(self, path: Path):
        self.path = path.with_name(path.name + ".lock")
        self.fd = None

    def __enter__(self):
        try:
            import fcntl

            self.fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(self.fd, fcntl.LOCK_EX)
        except (ImportError, OSError):
            self.fd = None
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            import fcntl

            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
        return False


def _write(path: Path, payload: Dict[str, Any]) -> None:
    payload.setdefault("actor", _ACTOR.get())
    _protect_questions(payload)
    key = str(path)
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _FileLock(path):
            _rotate_if_needed(path)
            # The cached hash is only valid while no other process appended
            # since our last write (same file size); otherwise read the tail.
            size = path.stat().st_size if path.exists() else 0
            cached = _LAST_HASH.get(key)
            if isinstance(cached, tuple) and cached[1] == size:
                prev = cached[0]
            else:
                prev = _tail_hash(path)
            payload["prev_hash"] = prev
            payload.pop("hash", None)
            payload["hash"] = _record_hash(prev, payload)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
            _LAST_HASH[key] = (payload["hash"], path.stat().st_size)


def _record_failure() -> None:
    _FAILURES["count"] += 1


def append_audit_event(kind: str, event: dict) -> None:
    try:
        path = _audit_path(kind)
        if path is None:
            return
        payload = _bounded(dict(event or {}))
        payload["timestamp"] = datetime.now(timezone.utc).isoformat()
        payload["kind"] = kind
        _write(path, payload)
    except Exception:
        _record_failure()
        return


def append_product_preview_chat_audit_event(event: dict) -> bool:
    try:
        if not getattr(config, "AUDIT_CHAT_ENABLED", True):
            return True
        path = Path(config.RUNS_DIR) / "audit" / "chat_events.jsonl"
        payload = _bounded(dict(event or {}))
        payload.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        payload["kind"] = "product_preview_chat"
        _write(path, payload)
        return True
    except Exception:
        _record_failure()
        return False


def append_feedback_audit_event(event: dict) -> bool:
    try:
        if not getattr(config, "AUDIT_CHAT_ENABLED", True):
            return True
        path = Path(config.RUNS_DIR) / "audit" / "feedback_events.jsonl"
        payload = _bounded(dict(event or {}))
        payload.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        payload["kind"] = "product_preview_feedback"
        _write(path, payload)
        return True
    except Exception:
        _record_failure()
        return False


def verify_chain(path: str | Path) -> Dict[str, Any]:
    """Recompute the chain of one file family (rotated files, then current)."""
    current = Path(path)
    files = sorted(current.parent.glob(f"{current.stem}.*{current.suffix}")) + ([current] if current.exists() else [])
    prev = _GENESIS
    checked = 0
    for file in files:
        with file.open("r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                record = json.loads(line)
                stored = record.pop("hash", None)
                if record.get("prev_hash") != prev or _record_hash(prev, record) != stored:
                    return {"ok": False, "file": str(file), "line": line_no, "checked": checked}
                prev = stored
                checked += 1
    return {"ok": True, "checked": checked, "files": [str(f) for f in files]}
