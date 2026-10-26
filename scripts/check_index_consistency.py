from __future__ import annotations
# --- bootstrap: add repo root to sys.path for script execution ---
import sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# --- end bootstrap ---

"""Compare a collection's keyword corpus with its vector rows.

Both indexes are written with the same index_generation per ingestion job.
This check reads the keyword JSONL of the collection, looks the same ids up
in the vector store and reports rows that are missing on one side or carry a
different generation. Exit code 1 when an inconsistency is found.

  python scripts/check_index_consistency.py --collection <name> [--sample 2000]
"""

import argparse
import json
from typing import Any, Dict, List, Optional

from rag_core import store
from rag_core.retrieval import keyword_index_path_for_collection


def _keyword_rows(path: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict) and str(obj.get("id") or "").strip():
                    rows.append(obj)
    except FileNotFoundError:
        pass
    return rows


def check_collection(collection_name: Optional[str], *, sample: int = 2000) -> Dict[str, Any]:
    path = keyword_index_path_for_collection(collection_name)
    rows = _keyword_rows(path)
    collection = store.get_vectorstore(
        collection_name=collection_name, create_if_missing=False, verify_embedding_fingerprint=False
    )
    checked = rows[: max(1, int(sample))] if rows else []
    by_id = {str(r["id"]): r for r in checked}
    found: Dict[str, Dict[str, Any]] = {}
    ids = list(by_id)
    for start in range(0, len(ids), 500):
        res = collection.get(ids=ids[start : start + 500], include=["metadatas"])
        for chunk_id, meta in zip(res.get("ids") or [], res.get("metadatas") or []):
            found[str(chunk_id)] = dict(meta or {})

    missing_parents = 0
    missing_other: List[str] = []
    mismatched: List[str] = []
    for chunk_id, row in by_id.items():
        meta = found.get(chunk_id)
        if meta is None:
            if str(row.get("chunk_role") or "") == "parent":
                missing_parents += 1  # expected with INGEST_EMBED_PARENT_CHUNKS=false
            else:
                missing_other.append(chunk_id)
            continue
        if str(row.get("index_generation") or "") != str(meta.get("index_generation") or ""):
            mismatched.append(chunk_id)
    vector_count = int(collection.count())
    report = {
        "collection": collection_name,
        "keyword_index_path": path,
        "keyword_rows": len(rows),
        "checked_rows": len(checked),
        "vector_rows": vector_count,
        "missing_in_vector": len(missing_other),
        "parents_not_in_vector": missing_parents,
        "generation_mismatch": len(mismatched),
        "examples": {"missing_in_vector": missing_other[:10], "generation_mismatch": mismatched[:10]},
    }
    report["consistent"] = not missing_other and not mismatched and bool(rows)
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--collection", default=None, help="vector collection (default: served collection)")
    parser.add_argument("--sample", type=int, default=2000)
    args = parser.parse_args(argv)
    report = check_collection(args.collection, sample=args.sample)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["consistent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
