from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from rag_core import embedding_provider


BUILDER_VERSION = "chroma_fingerprint_v1"
CORE_FINGERPRINT_KEYS = ("embed_provider", "embed_model")
EXTENDED_FINGERPRINT_KEYS = (
    "embed_provider",
    "embed_model",
    "embedding_dim",
    "source_jsonl_path",
    "source_jsonl_sha256",
    "chunk_count",
    "created_at",
    "builder_version",
)


def source_jsonl_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def active_core_fingerprint(provider_name: str | None = None) -> dict[str, str]:
    return embedding_provider.active_fingerprint(provider_name)


def build_collection_fingerprint_metadata(
    *,
    source_jsonl_path: str | Path | None = None,
    chunk_count: int | None = None,
    embedding_dim: int | None = None,
    provider_name: str | None = None,
    created_at: str | None = None,
    builder_version: str = BUILDER_VERSION,
) -> dict[str, Any]:
    payload: dict[str, Any] = dict(active_core_fingerprint(provider_name))
    if embedding_dim is not None:
        payload["embedding_dim"] = int(embedding_dim)
    if source_jsonl_path is not None:
        path = Path(source_jsonl_path)
        payload["source_jsonl_path"] = str(path)
        payload["source_jsonl_sha256"] = source_jsonl_sha256(path)
    if chunk_count is not None:
        payload["chunk_count"] = int(chunk_count)
    payload["created_at"] = created_at or datetime.now(timezone.utc).isoformat()
    payload["builder_version"] = builder_version
    return payload


def collection_core_fingerprint(metadata: Mapping[str, Any] | None) -> dict[str, str] | None:
    raw = dict(metadata or {})
    stamped = {key: str(raw.get(key) or "").strip() for key in CORE_FINGERPRINT_KEYS}
    if all(stamped.values()):
        return stamped
    return None


def collection_extended_fingerprint(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(metadata or {})
    return {key: raw[key] for key in EXTENDED_FINGERPRINT_KEYS if key in raw and raw[key] not in (None, "")}


def embedding_dim_from_collection(collection: Any) -> int | None:
    try:
        result = collection.get(include=["embeddings"], limit=1)
    except Exception:
        return None
    embeddings = result.get("embeddings") if isinstance(result, dict) else None
    if embeddings is None:
        return None
    try:
        if len(embeddings) == 0:
            return None
        first = embeddings[0]
        return len(first) if first is not None else None
    except Exception:
        return None


def stamp_collection_metadata(
    collection: Any,
    *,
    source_jsonl_path: str | Path | None = None,
    chunk_count: int | None = None,
    embedding_dim: int | None = None,
    provider_name: str | None = None,
    created_at: str | None = None,
    builder_version: str = BUILDER_VERSION,
) -> dict[str, Any]:
    if embedding_dim is None:
        embedding_dim = embedding_dim_from_collection(collection)
    metadata = dict(getattr(collection, "metadata", None) or {})
    metadata = {key: value for key, value in metadata.items() if not str(key).startswith("hnsw:")}
    stamp = build_collection_fingerprint_metadata(
        source_jsonl_path=source_jsonl_path,
        chunk_count=chunk_count,
        embedding_dim=embedding_dim,
        provider_name=provider_name,
        created_at=created_at,
        builder_version=builder_version,
    )
    metadata.update(stamp)
    collection.modify(metadata=metadata)
    return stamp


def fingerprint_status(metadata: Mapping[str, Any] | None, *, provider_name: str | None = None) -> dict[str, Any]:
    active = active_core_fingerprint(provider_name)
    stamped = collection_core_fingerprint(metadata)
    extended = collection_extended_fingerprint(metadata)
    return {
        "present": stamped is not None,
        "matches_active": stamped == active if stamped is not None else False,
        "active": active,
        "stamped": stamped,
        "extended": extended,
        "missing_extended_keys": [key for key in EXTENDED_FINGERPRINT_KEYS if key not in extended],
    }
