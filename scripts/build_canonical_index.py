from __future__ import annotations
# --- bootstrap: add repo root to sys.path for script execution ---
import sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# --- end bootstrap ---

import argparse
import json
import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from tools.dedup_chunks_by_id import _better

DEFAULT_OUTPUT_NAME = "chunks.canonical.bytype.dedup.jsonl"


def _iter_records(path: Path) -> Iterable[dict]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except Exception:
                continue
            if isinstance(obj, dict):
                yield obj


def collect_input_paths(index_dir: Path, output_path: Path) -> List[Path]:
    paths = []
    for path in sorted(index_dir.glob("*.jsonl")):
        if path.resolve() == output_path.resolve():
            continue
        paths.append(path)
    return paths


def inactive_sources_from_manifest(manifest_path: Path) -> Set[str]:
    """File names of deprecated/archived sources in a knowledge manifest."""
    from rag_core.knowledge_manifest import load_manifest

    manifest = load_manifest(manifest_path)
    names: Set[str] = set()
    for record in manifest.get("records") or manifest.get("sources") or []:
        if str(record.get("status") or "active") in {"deprecated", "archived"}:
            source_path = str(record.get("source_path") or "").strip()
            if source_path:
                names.add(Path(source_path).name)
    return names


def build_canonical_index(
    input_paths: List[Path],
    output_path: Path,
    *,
    inactive_sources: Optional[Set[str]] = None,
) -> Dict[str, int]:
    dedup: Dict[str, dict] = {}
    read = 0
    skipped_no_id = 0
    duplicates = 0
    excluded = 0
    for path in input_paths:
        for obj in _iter_records(path):
            read += 1
            chunk_id = str(obj.get("id") or "").strip()
            if not chunk_id:
                skipped_no_id += 1
                continue
            if inactive_sources and str(obj.get("source_doc") or "") in inactive_sources:
                excluded += 1
                continue
            previous = dedup.get(chunk_id)
            if previous is None:
                dedup[chunk_id] = obj
            else:
                duplicates += 1
                dedup[chunk_id] = _better(previous, obj)

    # Write to a temporary file and rename: the keyword index reloads on
    # mtime and must never read a half-written corpus.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(f".{output_path.name}.tmp-{os.getpid()}")
    with tmp_path.open("w", encoding="utf-8") as f:
        for chunk_id in sorted(dedup.keys()):
            f.write(json.dumps(dedup[chunk_id], ensure_ascii=False) + "\n")
    os.replace(tmp_path, output_path)
    stats = {
        "input_files": len(input_paths),
        "records_read": read,
        "skipped_no_id": skipped_no_id,
        "duplicates_merged": duplicates,
        "records_written": len(dedup),
    }
    if inactive_sources is not None:
        stats["excluded_inactive_sources"] = excluded
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Concatenate and deduplicate index/*.jsonl into the canonical keyword corpus."
    )
    parser.add_argument("--index-dir", default=str(_ROOT / "index"))
    parser.add_argument("--out", default=None)
    parser.add_argument(
        "--manifest",
        default=None,
        help="knowledge manifest; rows of deprecated/archived sources are left out",
    )
    args = parser.parse_args()

    index_dir = Path(args.index_dir)
    output_path = Path(args.out) if args.out else index_dir / DEFAULT_OUTPUT_NAME
    if not index_dir.is_dir():
        print(f"error: index dir not found: {index_dir}", file=sys.stderr)
        return 1
    input_paths = collect_input_paths(index_dir, output_path)
    if not input_paths:
        print(f"error: no input jsonl files in {index_dir}", file=sys.stderr)
        return 1

    inactive = inactive_sources_from_manifest(Path(args.manifest)) if args.manifest else None
    stats = build_canonical_index(input_paths, output_path, inactive_sources=inactive)
    print(json.dumps({"output": str(output_path), **stats}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
