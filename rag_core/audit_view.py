"""Reading the audit log for the console (S4): newest first, filtered, and checked for tampering.

The files are written by audit_log.py (one hash chain per file family: the current file and its rotated files). This module
only reads. A question in a chat event is already protected as AUDIT_QUESTION_MODE says; nothing is un-protected here.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import config
from rag_core import audit_log

# kind of the console -> file of the audit log
FILES = {"admin": "events.jsonl", "chat": "chat_events.jsonl", "feedback": "feedback_events.jsonl"}
MAX_SCAN = 50000


def _base(kind: str) -> Path:
    if kind not in FILES:
        raise KeyError(kind)
    return Path(config.RUNS_DIR) / "audit" / FILES[kind]


def _family(base: Path) -> List[Path]:
    """The files of one chain, oldest first."""
    return sorted(base.parent.glob(f"{base.stem}.*{base.suffix}")) + ([base] if base.exists() else [])


def _rows(kind: str) -> Iterable[Dict[str, Any]]:
    """Every line, newest first (up to MAX_SCAN lines)."""
    scanned = 0
    for path in reversed(_family(_base(kind))):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            scanned += 1
            if scanned > MAX_SCAN:
                return
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


def read_events(
    kind: str, *, limit: int = 100, offset: int = 0, text: Optional[str] = None, action: Optional[str] = None,
    tenants: Optional[Iterable[str]] = None, since: Optional[str] = None,
) -> Dict[str, Any]:
    """A page of events, newest first. `tenants` limits to an administrator's tenants (an event without a tenant is then hidden).
    `since` is an ISO time: older events are left out."""
    allowed = set(tenants) if tenants is not None else None
    wanted = (text or "").strip().lower()
    matched: List[Dict[str, Any]] = []
    for row in _rows(kind):
        stamp = str(row.get("timestamp") or "")
        if since and stamp and stamp < since:
            break  # newest first: everything after this is older
        if allowed is not None and row.get("tenant_id") not in allowed:
            continue
        if action and row.get("action") != action and row.get("kind") != action:
            continue
        if wanted and wanted not in json.dumps(row, ensure_ascii=False).lower():
            continue
        matched.append(row)
    start = max(0, int(offset))
    return {"kind": kind, "total": len(matched), "offset": start, "events": matched[start: start + max(1, min(int(limit), 500))]}


def verify(kind: str) -> Dict[str, Any]:
    """Recompute the hash chain of the files of `kind`: {"ok": True, "checked": n} or where it breaks."""
    base = _base(kind)
    if not _family(base):
        return {"ok": True, "checked": 0, "kind": kind}
    try:
        return {**audit_log.verify_chain(base), "kind": kind}
    except (OSError, ValueError) as exc:
        return {"ok": False, "kind": kind, "error": type(exc).__name__}


def daily_counts(kind: str, field: str, *, days: int = 14, tenants: Optional[Iterable[str]] = None) -> Dict[str, Dict[str, int]]:
    """For each day, how many events had each value of `field` (for example the type of feedback)."""
    allowed = set(tenants) if tenants is not None else None
    out: Dict[str, Dict[str, int]] = {}
    seen_days: List[str] = []
    for row in _rows(kind):
        stamp = str(row.get("timestamp") or "")[:10]
        if not stamp:
            continue
        if stamp not in seen_days:
            seen_days.append(stamp)
            if len(seen_days) > days:
                break
        if allowed is not None and row.get("tenant_id") not in allowed:
            continue
        value = str(row.get(field) or "unknown")
        bucket = out.setdefault(stamp, {})
        bucket[value] = bucket.get(value, 0) + 1
    return dict(sorted(out.items()))
