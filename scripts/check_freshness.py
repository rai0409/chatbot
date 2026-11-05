from __future__ import annotations
# --- bootstrap: add repo root to sys.path for script execution ---
import sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# --- end bootstrap ---

"""Knowledge freshness report (v2_09). Exit code 1 when action is needed.

Compares the knowledge manifest, the files on disk and the served keyword
index:
  changed_not_reingested  manifest checksum differs from the file on disk
  updated_after_index     updated_at is later than indexed_at
  missing_files           manifest entry whose file is gone
  stale                   active entries not updated for --max-age-days
  inactive_still_indexed  deprecated/archived sources still in the index
  indexed_not_in_manifest source_doc values in the index unknown to the manifest

  python scripts/check_freshness.py --manifest <knowledge_manifest.json> [--max-age-days 365]
Run it from cron/the monitoring runner and alert on a non-zero exit code.
"""

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

import config
from rag_core.knowledge_manifest import checksum_mismatches, load_manifest, missing_local_files


def _records(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list(manifest.get("records") or manifest.get("sources") or [])


def _indexed_sources(path: Path) -> Set[str]:
    sources: Set[str] = set()
    if not path.exists():
        return sources
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            source = str(row.get("source_doc") or "").strip()
            if source:
                sources.add(source)
    return sources


def _parse_time(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def freshness_report(
    manifest: Dict[str, Any],
    *,
    root_dir: Path,
    index_path: Path,
    max_age_days: int,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    records = _records(manifest)
    current = now or datetime.now(timezone.utc)
    active = [r for r in records if str(r.get("status") or "active") == "active"]
    inactive_names = {
        Path(str(r.get("source_path") or "")).name
        for r in records
        if str(r.get("status") or "active") in {"deprecated", "archived"}
    }
    manifest_names = {Path(str(r.get("source_path") or "")).name for r in records}
    indexed = _indexed_sources(index_path)

    updated_after_index: List[str] = []
    for record in active:
        updated, indexed_at = _parse_time(record.get("updated_at")), _parse_time(record.get("indexed_at"))
        if updated and indexed_at and updated > indexed_at:
            updated_after_index.append(str(record.get("source_id") or record.get("source_path")))

    stale: List[str] = []
    for record in active:
        updated = _parse_time(record.get("updated_at") or record.get("source_updated_at") or record.get("created_at"))
        if updated is None or current - updated > timedelta(days=max_age_days):
            stale.append(str(record.get("source_id") or record.get("source_path")))

    report = {
        "generated_at": current.isoformat(),
        "records": len(records),
        "active_records": len(active),
        "changed_not_reingested": list(checksum_mismatches(active, root_dir)),
        "updated_after_index": updated_after_index,
        "missing_files": list(missing_local_files(active, root_dir)),
        "stale": stale,
        "inactive_still_indexed": sorted(inactive_names & indexed),
        "indexed_not_in_manifest": sorted(indexed - manifest_names),
        "max_age_days": max_age_days,
    }
    report["action_needed"] = any(
        report[key]
        for key in ("changed_not_reingested", "updated_after_index", "missing_files", "stale", "inactive_still_indexed")
    )
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Report stale or unsynchronised knowledge sources.")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--root", default=str(config.BASE_DIR))
    parser.add_argument("--index", default=str(config.CHUNKS_JSONL_PATH))
    parser.add_argument("--max-age-days", type=int, default=365)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    report = freshness_report(
        load_manifest(args.manifest),
        root_dir=Path(args.root),
        index_path=Path(args.index),
        max_age_days=args.max_age_days,
    )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 1 if report["action_needed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
