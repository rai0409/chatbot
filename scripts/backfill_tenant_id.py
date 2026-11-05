#!/usr/bin/env python3
"""Backfill tenant_id="default" on vectorstore records that have none.

Required before TENANT_FILTER_STRICT=true: strict filtering matches the
default tenant inside the vector DB, so records without tenant_id would stop
appearing in default-tenant results. Dry-run unless --apply is given.
"""

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
from typing import Any, Dict, Optional, Sequence

from rag_core import store
from rag_core.retrieval import DEFAULT_TENANT_ID


def backfill_collection(collection: Any, *, apply: bool = False, batch_size: int = 500) -> Dict[str, Any]:
    total = int(collection.count())
    offset = 0
    missing = 0
    updated = 0
    while offset < total:
        res = collection.get(include=["metadatas"], limit=batch_size, offset=offset)
        ids = list(res.get("ids") or [])
        metas = list(res.get("metadatas") or [])
        if not ids:
            break
        fix_ids = []
        fix_metas = []
        for item_id, meta in zip(ids, metas):
            payload = dict(meta or {})
            if str(payload.get("tenant_id") or "").strip():
                continue
            payload["tenant_id"] = DEFAULT_TENANT_ID
            fix_ids.append(item_id)
            fix_metas.append(payload)
        missing += len(fix_ids)
        if apply and fix_ids:
            # Write the full metadata back so the result is the same whether
            # the installed Chroma merges or replaces metadata on update().
            collection.update(ids=fix_ids, metadatas=fix_metas)
            updated += len(fix_ids)
        offset += len(ids)
    return {
        "collection": str(getattr(collection, "name", "") or ""),
        "records": total,
        "missing_tenant_id": missing,
        "updated": updated,
        "applied": bool(apply),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", default=None, help="Chroma collection (default: configured collection)")
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args(argv)
    collection = store.get_vectorstore(
        collection_name=args.collection,
        verify_embedding_fingerprint=False,
        create_if_missing=False,
    )
    summary = backfill_collection(collection, apply=args.apply, batch_size=max(1, args.batch_size))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
