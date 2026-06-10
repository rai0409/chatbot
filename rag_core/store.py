from __future__ import annotations

import json
import logging
from typing import Dict, Optional

import chromadb

import config
from rag_core import embedding_provider

logger = logging.getLogger(__name__)

_FINGERPRINT_KEYS = ("embed_provider", "embed_model")

# Legacy collections without a fingerprint stamp already warned about, keyed by
# collection name, so the warning is emitted once per process instead of per query.
_UNSTAMPED_WARNED: set[str] = set()


def get_vectorstore(
    collection_name: Optional[str] = None,
    *,
    verify_embedding_fingerprint: bool = True,
):
    client = chromadb.PersistentClient(path=config.VECTORSTORE_DIR)
    name = (
        collection_name
        or config.getenv_first("CHROMA_COLLECTION")
        or config.VECTORSTORE_COLLECTION_NAME
        or "rag_chunks"
    )
    collection = client.get_or_create_collection(
        name=name, metadata={"hnsw:space": "cosine"}
    )
    if verify_embedding_fingerprint:
        verify_collection_fingerprint(collection)
    return collection


def collection_fingerprint(collection) -> Dict[str, str] | None:
    meta = getattr(collection, "metadata", None) or {}
    stamped = {key: str(meta.get(key) or "").strip() for key in _FINGERPRINT_KEYS}
    if all(stamped.values()):
        return stamped
    return None


def verify_collection_fingerprint(collection, provider_name: Optional[str] = None) -> None:
    name = str(getattr(collection, "name", "") or "unknown")
    stamped = collection_fingerprint(collection)
    if stamped is None:
        if name not in _UNSTAMPED_WARNED:
            _UNSTAMPED_WARNED.add(name)
            logger.warning(
                "embedding_fingerprint_missing %s",
                json.dumps(
                    {
                        "collection": name,
                        "active": embedding_provider.active_fingerprint(provider_name),
                        "reason": "collection_has_no_embedding_fingerprint_stamp",
                    },
                    ensure_ascii=False,
                ),
            )
        return
    active = embedding_provider.active_fingerprint(provider_name)
    if stamped != active:
        raise RuntimeError(
            f"embedding fingerprint mismatch for collection '{name}': "
            f"collection={stamped} active={active}. "
            "Re-ingest the collection with the active provider/model or set "
            "EMBED_PROVIDER and the embed model to match the collection."
        )


def stamp_collection_fingerprint(collection, provider_name: Optional[str] = None) -> Dict[str, str]:
    fingerprint = embedding_provider.active_fingerprint(provider_name)
    metadata = dict(getattr(collection, "metadata", None) or {})
    metadata.update(fingerprint)
    collection.modify(metadata=metadata)
    return fingerprint
