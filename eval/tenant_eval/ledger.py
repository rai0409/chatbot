"""An append-only, hash-chained record of evaluations, and the evaluation budget per evaluation set.

Every entry holds the hash of the entry before it, so an edited, removed or inserted line is found by verify_chain().
The budget counts how often one evaluation set (by its digest) was used to judge a change: each use is a chance to
pick a change that only fits that set. This is a local file that protects against accidents, not an audit log.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import config

LEDGER_SCHEMA = "tenant-eval-ledger-v1"
DEFAULT_BUDGET = 20
GENESIS = "0" * 64


class LedgerError(ValueError):
    pass


class BudgetError(LedgerError):
    pass


def ledger_path() -> Path:
    raw = str(os.getenv("TENANT_EVAL_LEDGER", "")).strip()
    return Path(raw) if raw else Path(config.RUNS_DIR) / "tenant_eval" / "ledger.jsonl"


def _digest(entry: Dict[str, Any]) -> str:
    body = {key: value for key, value in entry.items() if key != "sha256"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def read_ledger(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    path = Path(path) if path else ledger_path()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
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
    return rows


def verify_chain(path: Optional[Path] = None) -> int:
    """The number of entries, or LedgerError at the first entry that does not match its hash or its predecessor."""
    previous = GENESIS
    rows = read_ledger(path)
    for number, row in enumerate(rows, start=1):
        if row.get("prev") != previous or row.get("sha256") != _digest(row):
            raise LedgerError(f"ledger entry {number} does not match the chain")
        previous = row["sha256"]
    return len(rows)


def evaluations_used(eval_set_sha256: str, path: Optional[Path] = None) -> int:
    return sum(1 for row in read_ledger(path) if row.get("eval_set_sha256") == eval_set_sha256)


def check_budget(eval_set_sha256: str, *, budget: int = DEFAULT_BUDGET, path: Optional[Path] = None) -> int:
    """The evaluations left for this set, or BudgetError when the budget is spent."""
    used = evaluations_used(eval_set_sha256, path)
    if used >= budget:
        raise BudgetError(
            f"this evaluation set was already used for {used} evaluations (budget {budget}); "
            "write new golden cases before judging more changes"
        )
    return budget - used


def record(entry: Dict[str, Any], *, path: Optional[Path] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Append an entry (the caller's fields plus schema, time, the chain hashes). Returns what was written."""
    path = Path(path) if path else ledger_path()
    rows = read_ledger(path)
    verify_chain(path)
    full = {
        **entry,
        "schema": LEDGER_SCHEMA,
        "at": (now or datetime.now(timezone.utc)).isoformat(),
        "seq": len(rows) + 1,
        "prev": rows[-1]["sha256"] if rows else GENESIS,
    }
    full["sha256"] = _digest(full)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(full, ensure_ascii=False, sort_keys=True) + "\n")
    return full
