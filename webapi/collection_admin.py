"""Document deletion and staging -> production promotion (stage 15).

delete_documents: removes every chunk of the given documents (tenant_id +
  source_doc) from a collection's vector rows and its keyword corpus. A
  document removed at the source no longer stays searchable.
promote_staging: copies a ready staging collection's documents into the
  served (production) collection without re-embedding, replacing the same
  documents there, and merges their keyword rows into the served corpus.
  Both collections must use the active embedding model (the vector store
  checks the fingerprint when a collection is opened).

Both default to a dry run that reports what would change; execute=True
writes. Writes take the collection's write lock (the same as ingestion jobs).
Used by the admin API (webapi/main.py) and scripts/manage_documents.py.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import config
from webapi import ingestion_jobs

_BATCH = 256


def _mark_stale(collection: str) -> None:
    # The manifest of a generation that was just changed in place no longer matches its counts.
    from rag_core import index_manifest

    index_manifest.mark_stale(collection)


def _served_name() -> str:
    return config.resolve_chroma_collection_name()


def _keyword_path(collection: Optional[str]) -> str:
    from rag_core.retrieval import keyword_index_path_for_collection

    return keyword_index_path_for_collection(collection)


def _open(collection: Optional[str]):
    from rag_core import store

    name = collection or _served_name()
    if not ingestion_jobs._vectorstore_has_collection(name):
        raise LookupError(f"collection {name} does not exist in the vector store")
    return store.get_vectorstore(collection_name=collection, create_if_missing=False)


def _staging_owned(collection: str, tenant: str, *, ready: bool = False) -> None:
    name = ingestion_jobs.validate_collection_name(collection)
    entry = ingestion_jobs.get_collection(name)
    if entry is None:
        raise LookupError("staging collection is not registered")
    if entry["owner_tenant"] != tenant:
        raise PermissionError("collection is owned by another tenant")
    if ready and entry["status"] != ingestion_jobs.COLLECTION_READY:
        raise ValueError(f"staging collection is not ready (status {entry['status']})")


def _doc_where(tenant: str, source_doc: str) -> Dict[str, Any]:
    # Chroma's where syntax (the Qdrant wrapper accepts it too).
    return {"$and": [{"tenant_id": tenant}, {"source_doc": source_doc}]}


def _chunks(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def delete_documents(collection: Optional[str], *, tenant: str, source_docs: Sequence[str],
                     execute: bool = False) -> Dict[str, Any]:
    """collection None = the served collection."""
    from scripts.ingest_canonical_jsonl import remove_keyword_documents

    tenant = str(tenant or "").strip() or "default"
    docs = sorted({str(d).strip() for d in source_docs if str(d).strip()})
    if not docs:
        raise ValueError("source_docs is required")
    target = None if collection in (None, "") or ingestion_jobs.is_production_collection(collection) else collection
    if target is not None:
        _staging_owned(target, tenant)
    lock_name = target or _served_name()
    with ingestion_jobs._collection_write_lock(lock_name):
        vectors = _open(target)
        found: Dict[str, List[str]] = {}
        for doc in docs:
            ids = list(vectors.get(where=_doc_where(tenant, doc), include=[]).get("ids") or [])
            found[doc] = ids
        report = {
            "collection": target or "(served)", "tenant": tenant, "executed": bool(execute),
            "documents": {doc: len(ids) for doc, ids in found.items()},
            "chunks": sum(len(ids) for ids in found.values()),
        }
        if execute:
            for doc, ids in found.items():
                for part in _chunks(ids, 1000):
                    vectors.delete(ids=list(part), where=_doc_where(tenant, doc))
            keyword = remove_keyword_documents(_keyword_path(target), [(tenant, doc) for doc in docs])
            report["keyword_rows_removed"] = keyword.get("removed", 0)
            _mark_stale(target or _served_name())
    return report


def _rows(vectors, ids: Sequence[str]) -> Tuple[List[str], List[Any], List[str], List[Dict[str, Any]]]:
    res = vectors.get(ids=list(ids), include=["embeddings", "documents", "metadatas"])
    embeddings = res.get("embeddings")
    embeddings = [list(map(float, e)) for e in (embeddings if embeddings is not None else [])]
    return list(res.get("ids") or []), embeddings, list(res.get("documents") or []), list(res.get("metadatas") or [])


def promote_staging(collection: str, *, tenant: str, execute: bool = False) -> Dict[str, Any]:
    """Copy the staging collection's documents of `tenant` into the served collection."""
    tenant = str(tenant or "").strip() or "default"
    if ingestion_jobs.is_production_collection(collection):
        raise ValueError("refusing to promote the production collection into itself")
    _staging_owned(collection, tenant, ready=True)
    with ingestion_jobs._collection_write_lock(collection):  # no job rewrites it meanwhile
        return _promote_locked(collection, tenant, execute)


def _promote_locked(collection: str, tenant: str, execute: bool) -> Dict[str, Any]:
    from scripts.ingest_canonical_jsonl import (
        _delete_stale_document_rows,
        new_generation_id,
        read_keyword_documents,
        write_keyword_rows,
    )

    source = _open(collection)
    all_ids = list(source.get(where={"tenant_id": tenant}, include=[]).get("ids") or [])
    if not all_ids:
        raise ValueError("the staging collection has no rows of this tenant")
    doc_ids: Dict[Tuple[str, str], set] = {}
    for part in _chunks(all_ids, _BATCH):
        res = source.get(ids=list(part), include=["metadatas"])
        for chunk_id, meta in zip(res.get("ids") or [], res.get("metadatas") or []):
            doc = str((meta or {}).get("source_doc") or (meta or {}).get("doc_id") or "").strip()
            doc_ids.setdefault((tenant, doc), set()).add(chunk_id)
    report: Dict[str, Any] = {
        "collection": collection, "tenant": tenant, "executed": bool(execute),
        "documents": len(doc_ids), "chunks": len(all_ids),
    }
    served = _served_name()
    with ingestion_jobs._collection_write_lock(served):
        target = _open(None)
        replaced = 0
        for (doc_tenant, doc), ids in doc_ids.items():
            existing = set(target.get(where=_doc_where(doc_tenant, doc), include=[]).get("ids") or [])
            replaced += len(existing - ids)
        report["served_chunks_replaced"] = replaced
        if not execute:
            return report
        generation = new_generation_id()
        for part in _chunks(all_ids, _BATCH):
            ids, embeddings, documents, metadatas = _rows(source, part)
            for meta in metadatas:
                meta["index_generation"] = generation
            target.upsert(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)
        report["served_chunks_deleted"] = _delete_stale_document_rows(target, doc_ids)
        keyword_rows = read_keyword_documents(_keyword_path(collection), doc_ids.keys())
        keyword = write_keyword_rows(_keyword_path(None), keyword_rows, generation_id=generation)
        report.update({"keyword_rows_written": keyword["written"], "index_generation": generation})
        _mark_stale(served)
    return report
