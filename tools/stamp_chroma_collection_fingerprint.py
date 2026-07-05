#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rag_core import store
from rag_core.embedding_fingerprint import fingerprint_status, stamp_collection_metadata


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--collection", required=True)
    parser.add_argument("--source-jsonl", type=Path, required=True)
    parser.add_argument("--chunk-count", type=int, default=None)
    parser.add_argument("--embedding-dim", type=int, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    collection = store.get_vectorstore(
        collection_name=args.collection,
        verify_embedding_fingerprint=False,
        create_if_missing=False,
    )
    chunk_count = args.chunk_count if args.chunk_count is not None else int(collection.count())
    stamped = stamp_collection_metadata(
        collection,
        source_jsonl_path=args.source_jsonl,
        chunk_count=chunk_count,
        embedding_dim=args.embedding_dim,
    )
    status = fingerprint_status(getattr(collection, "metadata", None) or stamped)
    payload = {
        "collection": str(getattr(collection, "name", "") or args.collection),
        "stamped": stamped,
        "status": status,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
