#!/usr/bin/env python3
from __future__ import annotations
# --- bootstrap: add repo root to sys.path for script execution ---
import sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# --- end bootstrap ---

"""Compare two registered index generations.

Reports the embedding-profile differences, whether the vectors cannot be reused
(requires_reembedding), and how the chunk ids differ. Changed chunk ids are not an
error when the chunking schema changed; they are reported.

  python scripts/compare_index_generations.py --a OLD_COLLECTION --b NEW_COLLECTION
Exit code 1 when a manifest is missing.
"""

import argparse
import json
from typing import Any, Dict, List, Optional, Set

from rag_core import index_manifest, store


def _ids(collection_name: str) -> Set[str]:
    collection = store.get_vectorstore(
        collection_name=collection_name, create_if_missing=False, verify_embedding_fingerprint=False
    )
    ids: Set[str] = set()
    offset, page = 0, 1000
    while True:
        batch = collection.get(include=[], limit=page, offset=offset).get("ids") or []
        ids.update(str(i) for i in batch)
        offset += len(batch)
        if len(batch) < page:
            return ids


def compare_generations(a: str, b: str) -> Dict[str, Any]:
    first, second = index_manifest.read_manifest(a), index_manifest.read_manifest(b)
    missing = [name for name, manifest in ((a, first), (b, second)) if manifest is None]
    if missing:
        raise LookupError(f"no manifest for: {missing}")
    ids_a, ids_b = _ids(a), _ids(b)
    differing = {
        key: {"a": first["profile"].get(key), "b": second["profile"].get(key)}
        for key in index_manifest.PROFILE_KEYS
        if first["profile"].get(key) != second["profile"].get(key)
    }
    return {
        "a": a,
        "b": b,
        "profile_differences": differing,
        "requires_reembedding": index_manifest.requires_reembedding(first["profile"], second["profile"]),
        "chunking_schema": {"a": first.get("chunking_schema"), "b": second.get("chunking_schema")},
        "counts": {"a": len(ids_a), "b": len(ids_b), "common": len(ids_a & ids_b)},
        "same_ids": ids_a == ids_b,
        "only_in_a": sorted(ids_a - ids_b)[:20],
        "only_in_b": sorted(ids_b - ids_a)[:20],
        "only_in_a_count": len(ids_a - ids_b),
        "only_in_b_count": len(ids_b - ids_a),
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--a", required=True)
    parser.add_argument("--b", required=True)
    args = parser.parse_args(argv)
    try:
        report = compare_generations(args.a, args.b)
    except LookupError as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
