"""An append-only, hash-chained ledger of source scans and applies (step 5-1).

Each entry holds the hash of the entry before it, so an edited, removed or inserted line is found by verify_chain(). The
full change list of a run is stored in runs/<run id>.json beside the ledger; the entry carries its SHA-256 and the
first 100 changes. This is a local file that protects against accidents, not an audit log.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from rag_core.source_registry import SourceError, sources_dir

SCHEMA = "source-changes-v1"
GENESIS = "0" * 64
PREVIEW = 100


class LedgerError(SourceError):
    pass


def ledger_file() -> Path:
    return sources_dir() / "changes.jsonl"


def run_file(run_id: str) -> Path:
    if not run_id.isalnum():
        raise LedgerError("invalid run id")
    return sources_dir() / "runs" / f"{run_id}.json"


def _digest(entry: Dict[str, Any]) -> str:
    body = {key: value for key, value in entry.items() if key != "sha256"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def read_entries(source_id: Optional[str] = None, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    try:
        lines = ledger_file().read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    rows: List[Dict[str, Any]] = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise LedgerError(f"ledger line {number} is not JSON") from exc
        if not isinstance(row, dict):
            raise LedgerError(f"ledger line {number} is not an object")
        rows.append(row)
    if source_id is not None:
        rows = [row for row in rows if row.get("source_id") == source_id]
    return rows[-limit:] if limit else rows


def verify_chain() -> int:
    """The number of entries, or LedgerError at the first entry that does not match its hash or its predecessor."""
    previous = GENESIS
    rows = read_entries()
    for number, row in enumerate(rows, start=1):
        if row.get("prev") != previous or row.get("sha256") != _digest(row):
            raise LedgerError(f"ledger entry {number} does not match the chain")
        previous = row["sha256"]
    return len(rows)


def record(
    kind: str, *, source_id: str, tenant_id: str, summary: Dict[str, Any],
    changes: Optional[Sequence[Dict[str, Any]]] = None, run_id: Optional[str] = None, now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Append an entry of `kind` ("scan", "scan_failed", "apply", "hold", "approve", ...). Returns what was written."""
    rows = read_entries()
    verify_chain()
    run = run_id or uuid.uuid4().hex[:12]
    entry: Dict[str, Any] = {
        "schema": SCHEMA, "seq": len(rows) + 1, "at": (now or datetime.now(timezone.utc)).isoformat(), "kind": kind,
        "source_id": source_id, "tenant_id": tenant_id, "run_id": run, "summary": summary,
        "prev": rows[-1]["sha256"] if rows else GENESIS,
    }
    if changes is not None:
        body = json.dumps(list(changes), ensure_ascii=False, sort_keys=True)
        entry["changes_sha256"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
        entry["changes_count"] = len(changes)
        entry["changes_preview"] = list(changes[:PREVIEW])
        target = run_file(run)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.tmp-{os.getpid()}")
        tmp.write_text(body + "\n", encoding="utf-8")
        os.replace(tmp, target)
    entry["sha256"] = _digest(entry)
    path = ledger_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return entry


def read_changes(run_id: str) -> List[Dict[str, Any]]:
    """The full change list of a run, checked against the hash in its ledger entry."""
    entry = next((e for e in read_entries() if e.get("run_id") == run_id and "changes_sha256" in e), None)
    if entry is None:
        raise LedgerError(f"no stored changes for run {run_id}")
    try:
        body = run_file(run_id).read_text(encoding="utf-8").rstrip("\n")
    except OSError as exc:
        raise LedgerError(f"the change list of run {run_id} is missing") from exc
    if hashlib.sha256(body.encode("utf-8")).hexdigest() != entry["changes_sha256"]:
        raise LedgerError(f"the change list of run {run_id} does not match the ledger")
    return json.loads(body)
