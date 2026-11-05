"""Index manifest: what one generation (a collection plus its keyword corpus) was built from.

A generation is a vector collection together with its own keyword corpus
(INDEX_DIR/collections/<name>.jsonl). The manifest records the embedding
profile, counts and verification state so that two generations can be compared
and a generation is switched to (rag_core.index_generation) only when it is
complete and matches the running embedding provider.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import config

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
PROFILE_KEYS = (
    "embed_provider",
    "embed_model",
    "embed_prefix_scheme",
    "embed_normalize",
    "embed_distance",
    "embedding_dim",
)
STATUS_UNVERIFIED = "unverified"
STATUS_VERIFIED = "verified"
STATUS_STALE = "stale"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def generations_dir() -> Path:
    return Path(config.INDEX_DIR) / "generations"


def _safe_name(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    if safe != name:
        safe = f"{safe}-{hashlib.sha256(name.encode('utf-8')).hexdigest()[:10]}"
    return safe


def manifest_path(collection: str) -> Path:
    name = str(collection or "").strip()
    if not name:
        raise ValueError("collection name is required")
    return generations_dir() / f"{_safe_name(name)}.manifest.json"


def read_manifest(collection: str) -> Optional[Dict[str, Any]]:
    name = str(collection or "").strip()
    path = manifest_path(name)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except ValueError as exc:
        raise ValueError(f"manifest {path} is not valid JSON") from exc
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION or data.get("collection") != name:
        raise ValueError(f"manifest {path} has an unsupported schema or belongs to another collection")
    return data


def write_manifest(manifest: Dict[str, Any]) -> Path:
    path = manifest_path(str(manifest["collection"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def collection_profile(collection: Any) -> Dict[str, Any]:
    """The embedding profile stamped on a collection (None for a missing stamp)."""
    meta = dict(getattr(collection, "metadata", None) or {})
    profile: Dict[str, Any] = {}
    for key in PROFILE_KEYS:
        value = meta.get(key)
        if value in (None, ""):
            profile[key] = None
        elif key == "embedding_dim":
            try:
                profile[key] = int(value)
            except (TypeError, ValueError):
                profile[key] = None
        else:
            profile[key] = str(value)
    # Reserved: the model loaders do not pin a revision yet.
    profile["embed_model_revision"] = None
    return profile


def requires_reembedding(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """True when vectors of one profile cannot be reused for the other."""
    return any(a.get(key) != b.get(key) for key in PROFILE_KEYS)


def count_keyword_rows(path: str) -> int:
    rows = 0
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict) and str(obj.get("id") or "").strip():
                    rows += 1
    except FileNotFoundError:
        return 0
    return rows


def build_manifest(
    collection_name: str,
    *,
    collection: Any = None,
    keyword_index_path: Optional[str] = None,
    derived_from: Optional[str] = None,
    reembedded: bool = False,
    source_jsonl_path: Optional[str] = None,
    chunking_schema: Optional[str] = None,
) -> Dict[str, Any]:
    from rag_core import embedding_fingerprint, store, vector_backend
    from rag_core.retrieval import collection_corpus_path

    name = str(collection_name or "").strip()
    if collection is None:
        collection = store.get_vectorstore(
            collection_name=name, create_if_missing=False, verify_embedding_fingerprint=False
        )
    path = str(keyword_index_path or collection_corpus_path(name))
    profile = collection_profile(collection)
    if derived_from:
        source = read_manifest(derived_from)
        if source is None:
            raise ValueError(f"derived_from '{derived_from}' has no manifest; register it first")
        if requires_reembedding(source["profile"], profile) and not reembedded:
            raise ValueError(
                f"the embedding profile of '{name}' differs from '{derived_from}': vectors cannot be copied "
                "across profiles. Re-embed the documents and register with reembedded=True."
            )
    return {
        "schema_version": SCHEMA_VERSION,
        "generation_id": uuid.uuid4().hex,
        "collection": name,
        "backend": vector_backend.backend_name(),
        "keyword_index_path": path,
        "created_at": now_iso(),
        "profile": profile,
        "point_count": int(collection.count()),
        "keyword_rows": count_keyword_rows(path),
        "source_jsonl_sha256": embedding_fingerprint.source_jsonl_sha256(source_jsonl_path) if source_jsonl_path else None,
        "chunking_schema": chunking_schema,
        "derived_from": derived_from,
        "reembedded": bool(reembedded),
        "verification": {"status": STATUS_UNVERIFIED, "checked_at": None},
    }


def mark_stale(collection_name: str) -> bool:
    """A generation changed in place (ingest, promotion, deletion): its manifest counts are out of date.

    No manifest -> nothing to do. Never raises: an unreadable manifest must not fail an ingest."""
    try:
        manifest = read_manifest(collection_name)
        if manifest is None:
            return False
        manifest["verification"] = {"status": STATUS_STALE, "checked_at": None, "marked_at": now_iso()}
        write_manifest(manifest)
        return True
    except (OSError, ValueError):
        logger.warning("could not mark the manifest of %s as stale", collection_name, exc_info=True)
        return False
