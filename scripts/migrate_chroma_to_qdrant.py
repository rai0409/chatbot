from __future__ import annotations
# --- bootstrap: add repo root to sys.path for script execution ---
import sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# --- end bootstrap ---

"""Copy a Chroma collection into Qdrant without re-embedding (v2_06).

Vectors, documents, metadata and the collection metadata (embedding
fingerprint) are copied as they are, so the same embedding model must be used
afterwards. Verifies the point count at the end; the Chroma collection is not
changed.

  python scripts/migrate_chroma_to_qdrant.py --collection <name> [--target <name>] [--batch 256] [--dry-run]
Requires requirements-qdrant.txt and QDRANT_URL (or QDRANT_PATH).
"""

import argparse
import json
from typing import Any, Dict, List, Optional, Sequence

import chromadb

import config
from rag_core.vector_backend import QdrantCollection


def migrate(collection_name: str, *, target_name: Optional[str] = None, batch: int = 256, dry_run: bool = False) -> Dict[str, Any]:
    client = chromadb.PersistentClient(path=config.VECTORSTORE_DIR)
    source = client.get_collection(name=collection_name)
    total = int(source.count())
    target = QdrantCollection(target_name or collection_name)
    copied = 0
    offset = 0
    while offset < total:
        res = source.get(limit=batch, offset=offset, include=["documents", "metadatas", "embeddings"])
        ids: List[str] = list(res.get("ids") or [])
        if not ids:
            break
        embeddings = res.get("embeddings")
        if embeddings is None or len(embeddings) != len(ids):
            raise RuntimeError("chroma returned rows without embeddings; cannot migrate without re-embedding")
        if not dry_run:
            target.upsert(
                ids=ids,
                documents=list(res.get("documents") or [""] * len(ids)),
                embeddings=[list(map(float, e)) for e in embeddings],
                metadatas=list(res.get("metadatas") or [{}] * len(ids)),
            )
        copied += len(ids)
        offset += len(ids)
    metadata = {k: v for k, v in dict(source.metadata or {}).items() if not str(k).startswith("hnsw:")}
    if not dry_run:
        target.modify(metadata=metadata)
    target_count = target.count() if not dry_run else None
    report = {
        "source": collection_name,
        "target": target.name,
        "source_count": total,
        "copied": copied,
        "target_count": target_count,
        "dry_run": dry_run,
        "metadata_keys": sorted(metadata),
    }
    report["ok"] = dry_run or target_count == total
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Copy a Chroma collection into Qdrant.")
    parser.add_argument("--collection", required=True)
    parser.add_argument("--target", default=None)
    parser.add_argument("--batch", type=int, default=256)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    report = migrate(args.collection, target_name=args.target, batch=args.batch, dry_run=args.dry_run)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
