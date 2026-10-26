from __future__ import annotations

import json
import logging
from threading import Lock
from typing import Any, Dict, Optional

import chromadb

import config
from rag_core import embedding_provider

logger = logging.getLogger(__name__)

_FINGERPRINT_KEYS = ("embed_provider", "embed_model")
# Stamped by rag_core.embedding_fingerprint.stamp_collection_metadata. Checked
# only when present, so collections stamped before prefixes existed still load.
_PREFIX_SCHEME_KEY = "embed_prefix_scheme"

# Reused chromadb.PersistentClient instances keyed by resolved vectorstore path,
# so each query does not pay client construction cost.
_CLIENT_CACHE: Dict[str, Any] = {}
_CLIENT_LOCK = Lock()


def _get_persistent_client(path: str):
    with _CLIENT_LOCK:
        client = _CLIENT_CACHE.get(path)
        if client is None:
            client = chromadb.PersistentClient(path=path)
            _CLIENT_CACHE[path] = client
        return client


def reset_vectorstore_clients() -> None:
    with _CLIENT_LOCK:
        _CLIENT_CACHE.clear()

# Legacy collections without a fingerprint stamp already warned about, keyed by
# collection name, so the warning is emitted once per process instead of per query.
_UNSTAMPED_WARNED: set[str] = set()


def get_vectorstore(
    collection_name: Optional[str] = None,
    *,
    verify_embedding_fingerprint: bool = True,
    create_if_missing: bool = True,
):
    name = config.resolve_chroma_collection_name(collection_name)
    from rag_core import vector_backend

    if vector_backend.backend_name() == "qdrant":
        # VECTOR_BACKEND=qdrant: same interface, Qdrant underneath.
        collection = vector_backend.get_collection(name, create_if_missing=create_if_missing)
        if verify_embedding_fingerprint:
            verify_collection_fingerprint(collection)
        return collection
    client = _get_persistent_client(config.VECTORSTORE_DIR)
    if create_if_missing:
        collection = client.get_or_create_collection(
            name=name, metadata={"hnsw:space": "cosine"}
        )
    else:
        collection = client.get_collection(name=name)
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
    # Same model with a different query/document prefix scheme yields
    # incompatible vectors (e.g. ruri-v3 "検索クエリ: " vs no prefix).
    meta = getattr(collection, "metadata", None) or {}
    stamped_scheme = str(meta.get(_PREFIX_SCHEME_KEY) or "").strip()
    if stamped_scheme:
        active_scheme = embedding_provider.active_prefix_scheme(provider_name)
        if stamped_scheme != active_scheme:
            raise RuntimeError(
                f"embedding prefix scheme mismatch for collection '{name}': "
                f"collection={stamped_scheme} active={active_scheme}. "
                "Re-ingest the collection or set EMBED_QUERY_PREFIX/EMBED_DOCUMENT_PREFIX "
                "to match the collection."
            )


def stamp_collection_fingerprint(collection, provider_name: Optional[str] = None) -> Dict[str, str]:
    fingerprint = embedding_provider.active_fingerprint(provider_name)
    metadata = dict(getattr(collection, "metadata", None) or {})
    # chromadb rejects any "hnsw:*" key in modify() (the distance function is
    # immutable after creation), so echoing the creation metadata back made
    # every stamp attempt fail — strip those keys and only write the
    # fingerprint metadata.
    metadata = {k: v for k, v in metadata.items() if not str(k).startswith("hnsw:")}
    metadata.update(fingerprint)
    collection.modify(metadata=metadata)
    return fingerprint
