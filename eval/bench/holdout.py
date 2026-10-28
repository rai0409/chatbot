"""The held-out evaluation split ("test") may be used once.

Tuning on the test split turns it into a tuning set, and its numbers stop being an estimate of
performance on unseen questions. runner.run therefore refuses split "test" unless the config says
final_test=true (CLI: --final-test), and records every use in a ledger: one JSON line per use,
keyed by the SHA-256 of the evaluation set. A second use of the same evaluation set is refused
unless allow_test_reuse=true (CLI: --allow-test-reuse); that re-use is recorded as such.

The ledger (default runs/bench/test_access.jsonl, or cfg["test_ledger"]) protects against accidents.
It is a local file, not an audit log.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import config

LEDGER_SCHEMA = "bench-test-ledger-v1"


class HoldoutError(ValueError):
    """The held-out split was requested in a way that would spoil it."""


def ledger_path(cfg: Dict[str, Any]) -> Path:
    raw = cfg.get("test_ledger")
    return Path(raw) if raw else Path(config.RUNS_DIR) / "bench" / "test_access.jsonl"


def read_ledger(path: Path) -> List[Dict[str, Any]]:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    rows: List[Dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise HoldoutError(f"{path} has a line that is not JSON; repair or archive the ledger first") from exc
        if isinstance(row, dict):
            rows.append(row)
    return rows


def claim_test_split(
    cfg: Dict[str, Any], eval_set_sha256: str, *, run_id: str, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Called before a run that reads split "test". Returns the ledger entry that was written."""
    if cfg.get("final_test") is not True:
        raise HoldoutError(
            "split 'test' is the held-out evaluation set: tune on split 'dev', and use 'test' once at the end "
            "with final_test=true (CLI: --final-test)."
        )
    path = ledger_path(cfg)
    previous = [
        row for row in read_ledger(path) if row.get("eval_set_sha256") == eval_set_sha256 and row.get("split") == "test"
    ]
    if previous and cfg.get("allow_test_reuse") is not True:
        first = previous[0]
        raise HoldoutError(
            f"the test split of this evaluation set was already used ({len(previous)} time(s); first in run "
            f"{first.get('run_id')} at {first.get('at')}). A second use turns it into a tuning set. "
            "Pass allow_test_reuse=true (CLI: --allow-test-reuse) to record a deliberate re-use."
        )
    entry = {
        "schema": LEDGER_SCHEMA,
        "eval_set_sha256": eval_set_sha256,
        "split": "test",
        "run_id": run_id,
        "at": (now or datetime.now(timezone.utc)).isoformat(),
        "reused": bool(previous),
        "uses_before": len(previous),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return entry
