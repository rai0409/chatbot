from __future__ import annotations

"""Synonym candidate review for operators (N8).

The candidate file is fixed: RUNS_DIR/synonyms/candidates.jsonl, written by
scripts/extract_synonym_candidates.py. Operators set each candidate to
approved / rejected / candidate on the admin page; "apply" merges approved
rows into a RAG profile's synonyms.json (scripts/apply_synonym_candidates.py).
Only approved entries are ever used for search (query_preprocess).
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional

import config

STATUSES = {"approved", "rejected", "candidate"}
_LOCK = Lock()
_MAX_LIST = 1000


def candidates_path() -> Path:
    return Path(config.RUNS_DIR) / "synonyms" / "candidates.jsonl"


def _read() -> List[Dict[str, Any]]:
    path = candidates_path()
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("candidate_id"):
            rows.append(row)
    return rows


def _write(rows: List[Dict[str, Any]]) -> None:
    path = candidates_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def list_candidates(status: Optional[str] = None, limit: int = 200) -> Dict[str, Any]:
    rows = _read()
    counts: Dict[str, int] = {}
    for row in rows:
        counts[str(row.get("status") or "candidate")] = counts.get(str(row.get("status") or "candidate"), 0) + 1
    if status:
        rows = [r for r in rows if str(r.get("status") or "candidate") == status]
    safe_limit = max(1, min(int(limit or 200), _MAX_LIST))
    items = [
        {
            "candidate_id": r.get("candidate_id"),
            "method": r.get("method"),
            "canonical": r.get("canonical"),
            "synonyms": list(r.get("synonyms") or [])[:10],
            "status": r.get("status") or "candidate",
            "decided_by": r.get("decided_by"),
            "decided_at": r.get("decided_at"),
        }
        for r in rows[:safe_limit]
    ]
    return {"items": items, "counts": counts, "total": len(rows)}


def decide(candidate_id: str, status: str, *, decided_by: Optional[str] = None) -> Dict[str, Any]:
    if status not in STATUSES:
        raise ValueError("status must be approved, rejected or candidate")
    with _LOCK:
        rows = _read()
        for row in rows:
            if row.get("candidate_id") == candidate_id:
                row["status"] = status
                row["decided_by"] = decided_by
                row["decided_at"] = datetime.now(timezone.utc).isoformat()
                _write(rows)
                return {"candidate_id": candidate_id, "status": status}
    raise KeyError(candidate_id)


def apply_to_profile(profile_id: str) -> Dict[str, Any]:
    from rag_core.profile_loader import PROFILE_ROOT, is_valid_rag_profile_id, reload_rag_profile_cache
    from scripts.apply_synonym_candidates import merge_synonyms

    if not is_valid_rag_profile_id(profile_id):
        raise ValueError("invalid profile id")
    approved = [r for r in _read() if r.get("status") == "approved"]
    target = PROFILE_ROOT / profile_id / "exported" / "synonyms.json"
    existing = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {"synonyms": []}
    merged = merge_synonyms(existing, approved)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    reload_rag_profile_cache()
    return {
        "profile_id": profile_id,
        "approved_rows": len(approved),
        "entries_before": len(existing.get("synonyms") or []),
        "entries_after": len(merged["synonyms"]),
    }
