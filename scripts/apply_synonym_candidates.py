from __future__ import annotations
# --- bootstrap: add repo root to sys.path for script execution ---
import sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# --- end bootstrap ---

"""Merge reviewed synonym candidates into a RAG profile (N8).

Only rows with "status": "approved" are applied. They are added to
config/rag_profiles/<profile>/exported/synonyms.json as enabled entries;
existing entries with the same canonical get the new synonyms merged in.
The file is replaced atomically. Run with --dry-run first.

  python scripts/apply_synonym_candidates.py --candidates <reviewed.jsonl> --profile <profile_id> [--dry-run]
"""

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from rag_core.profile_loader import PROFILE_ROOT, is_valid_rag_profile_id, pin_state, reload_rag_profile_cache


def _approved_rows(path: Path) -> List[Dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("status") == "approved" and str(row.get("canonical") or "").strip():
                rows.append(row)
    return rows


def merge_synonyms(existing: Dict[str, Any], approved: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    entries = list(existing.get("synonyms") or [])
    by_canonical = {str(e.get("canonical")): e for e in entries if isinstance(e, dict)}
    for row in approved:
        canonical = str(row["canonical"]).strip()
        new_terms = [str(t).strip() for t in row.get("synonyms") or [] if str(t).strip() and str(t).strip() != canonical]
        entry = by_canonical.get(canonical)
        if entry is None:
            entry = {
                "canonical": canonical,
                "synonyms": [],
                "description": f"承認済み候補 ({row.get('method')}, {row.get('candidate_id')})",
                "enabled": True,
            }
            entries.append(entry)
            by_canonical[canonical] = entry
        for term in new_terms:
            if term not in entry["synonyms"]:
                entry["synonyms"].append(term)
    return {**existing, "synonyms": entries}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Apply approved synonym candidates to a RAG profile.")
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if not is_valid_rag_profile_id(args.profile):
        print("error: invalid profile id", file=sys.stderr)
        return 1
    target = PROFILE_ROOT / args.profile / "exported" / "synonyms.json"
    existing = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {"synonyms": []}
    approved = _approved_rows(Path(args.candidates))
    merged = merge_synonyms(existing, approved)
    summary = {
        "profile": args.profile,
        "approved_rows": len(approved),
        "entries_before": len(existing.get("synonyms") or []),
        "entries_after": len(merged["synonyms"]),
        "dry_run": bool(args.dry_run),
        # when a version is pinned, this edit of config/ is live only after a new version is created and activated
        "pinned_version": pin_state(args.profile) or None,
    }
    if not args.dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.tmp-{os.getpid()}")
        tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, target)
        reload_rag_profile_cache()
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
