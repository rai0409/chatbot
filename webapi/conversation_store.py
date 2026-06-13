from __future__ import annotations

# Smallest safe local conversation-history boundary (Prompt042).
#
# Per-(tenant, identity)-isolated thread storage as JSONL files under
# RUNS_DIR/conversations/<tenant>/<identity>/<thread_id>.jsonl. Isolation is
# enforced structurally by the path (tenant + identity fingerprint are part of
# the directory), and every read is scoped to the caller's (tenant, identity).
#
# Does NOT reuse the vector store. Stores only the caller's own question/answer
# text (scoped to them) plus safe metadata — never API keys, SSO secrets, trust
# tokens, or another tenant's data. Default-off as a runtime surface; the chat
# path is unchanged unless CONVERSATION_HISTORY_ENABLED is set.

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import config


_ENABLED_VALUES = {"1", "true", "yes", "on"}
_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9_.-]")
_DEFAULT_MAX_THREADS = 50
_DEFAULT_MAX_AGE_DAYS = 90
_MAX_TURNS_PER_THREAD = 500
_MAX_TEXT_CHARS = 8000


def conversation_history_enabled() -> bool:
    return str(os.getenv("CONVERSATION_HISTORY_ENABLED", "")).strip().lower() in _ENABLED_VALUES


def _max_threads() -> int:
    try:
        v = int(os.getenv("CONVERSATION_HISTORY_MAX_THREADS", "") or _DEFAULT_MAX_THREADS)
        return v if v > 0 else _DEFAULT_MAX_THREADS
    except ValueError:
        return _DEFAULT_MAX_THREADS


def _max_age_days() -> int:
    try:
        v = int(os.getenv("CONVERSATION_HISTORY_MAX_AGE_DAYS", "") or _DEFAULT_MAX_AGE_DAYS)
        return v if v > 0 else _DEFAULT_MAX_AGE_DAYS
    except ValueError:
        return _DEFAULT_MAX_AGE_DAYS


def _safe_segment(value: str, *, fallback: str) -> str:
    text = _SAFE_SEGMENT.sub("_", str(value or "").strip())
    text = text.strip("._")[:128]
    return text or fallback


def _root() -> Path:
    return Path(config.RUNS_DIR) / "conversations"


def _owner_dir(tenant_id: str, identity: str) -> Path:
    # tenant + identity are sanitized into path segments -> structural isolation.
    return _root() / _safe_segment(tenant_id, fallback="default") / _safe_segment(identity, fallback="anonymous")


def _thread_path(tenant_id: str, identity: str, thread_id: str) -> Path:
    return _owner_dir(tenant_id, identity) / f"{_safe_segment(thread_id, fallback='thread')}.jsonl"


def _clock() -> float:
    return time.time()


def append_turn(
    tenant_id: str,
    identity: str,
    thread_id: str,
    *,
    question: str,
    answer_text: str = "",
    answer_mode: Optional[str] = None,
    abstained: bool = False,
    citations_count: int = 0,
) -> Dict[str, Any]:
    # Persist one Q/A turn scoped to (tenant, identity). Only the caller's own
    # text is stored; no keys/secrets. Returns the stored record.
    path = _thread_path(tenant_id, identity, thread_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": _clock(),
        "thread_id": _safe_segment(thread_id, fallback="thread"),
        "question": str(question or "")[:_MAX_TEXT_CHARS],
        "answer_text": str(answer_text or "")[:_MAX_TEXT_CHARS],
        "answer_mode": str(answer_mode) if answer_mode else None,
        "abstained": bool(abstained),
        "citations_count": int(citations_count or 0),
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    purge(tenant_id, identity)
    return record


def get_thread(tenant_id: str, identity: str, thread_id: str) -> List[Dict[str, Any]]:
    # Reads are scoped to (tenant, identity) by path construction; another
    # tenant/identity can never reach this owner's directory.
    path = _thread_path(tenant_id, identity, thread_id)
    if not path.exists():
        return []
    turns: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            turns.append(json.loads(line))
        except Exception:
            continue
    return turns[-_MAX_TURNS_PER_THREAD:]


def list_threads(tenant_id: str, identity: str) -> List[Dict[str, Any]]:
    owner = _owner_dir(tenant_id, identity)
    if not owner.exists():
        return []
    out: List[Dict[str, Any]] = []
    for path in owner.glob("*.jsonl"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        first_q = ""
        turns = 0
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                turns += 1
                if not first_q:
                    try:
                        first_q = str(json.loads(line).get("question") or "")[:120]
                    except Exception:
                        first_q = ""
        except Exception:
            continue
        out.append({
            "thread_id": path.stem,
            "updated_at": mtime,
            "turns": turns,
            "title": first_q,
        })
    out.sort(key=lambda t: t["updated_at"], reverse=True)
    return out


def delete_thread(tenant_id: str, identity: str, thread_id: str) -> bool:
    path = _thread_path(tenant_id, identity, thread_id)
    if path.exists():
        path.unlink()
        return True
    return False


def purge(
    tenant_id: str,
    identity: str,
    *,
    max_threads: Optional[int] = None,
    max_age_days: Optional[int] = None,
) -> int:
    # Retention: drop threads older than max_age_days, then keep only the most
    # recent max_threads. Returns the number of threads removed.
    owner = _owner_dir(tenant_id, identity)
    if not owner.exists():
        return 0
    cap = max_threads if max_threads is not None else _max_threads()
    age_days = max_age_days if max_age_days is not None else _max_age_days()
    cutoff = _clock() - age_days * 86400
    removed = 0
    paths = sorted(owner.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    kept: List[Path] = []
    for path in paths:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            path.unlink()
            removed += 1
        else:
            kept.append(path)
    for path in kept[cap:]:
        path.unlink()
        removed += 1
    return removed
