"""Document deletion and staging -> production promotion (stage 15; 2026-11 ops hardening).

delete_documents: removes every chunk of the given documents (tenant_id +
  source_doc, legacy rows matched by doc_id) from a collection's vector rows and
  its keyword corpus. A tombstone (tenant, source_doc, deleted_at, reason) is
  written FIRST, so promotion and generation switches converge even after a
  crash; a document removed at the source no longer stays searchable.
promote_staging: copies a ready staging collection's documents into the
  served (production) collection without re-embedding, replacing the same
  documents there, and merges their keyword rows into the served corpus.
  Tombstoned documents are removed from the served collection and never copied.
  Both collections must use the active embedding model (the vector store
  checks the fingerprint when a collection is opened).

Both default to a dry run that reports what would change; execute=True
writes. Writes take the collection's write lock (the same as ingestion jobs),
the maintenance lock in shared mode (a running backup holds it exclusively),
and are recorded as an operation (running -> done / failed; a crash leaves
"running", marked "interrupted" at startup) in the ingestion registry. The
served manifest is marked stale before the first write.

Promotion streams: a metadata-only pass validates everything and maps
documents to ids, then each batch of _BATCH rows is read with its embeddings
and upserted at once; the keyword rows are streamed into the corpus rewrite.
Used by the admin API (webapi/main.py) and scripts/manage_documents.py.
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import ExitStack, contextmanager
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

import config
from rag_core.mail_access import is_private_mail_record
from webapi import ingestion_jobs

logger = logging.getLogger(__name__)

_BATCH = 256
_TYPE_FIELDS = ("type", "source_type", "doc_type")
_STRING_FIELDS = ("type", "source_type", "doc_type", "source_doc", "doc_id", "id", "tenant_id")
# PROMOTION_ALLOW_UNTYPED=true: rows without any type field (legacy general documents) are promoted when their
# document name has one of these extensions. Off by default: an untyped row is refused (fail-closed).
_GENERAL_DOC_EXTENSIONS = (".pdf", ".docx", ".xlsx", ".xlsm", ".pptx", ".csv", ".txt", ".md", ".html", ".htm")
_MAX_STATS = {"buffered_rows": 0}  # the largest batch read with embeddings (tests check that promotion streams)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}  # the set of config._get_bool


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


def _doc_ids(store, tenant: str, doc: str) -> List[str]:
    from scripts.ingest_canonical_jsonl import document_row_ids

    return document_row_ids(store, tenant, doc)


def _clear_answer_cache(tenant: str) -> None:
    """Cached answers may quote a document that was just deleted or replaced."""
    try:
        from rag_core import answer_cache

        answer_cache.clear_tenant(tenant)
    except Exception:  # noqa: BLE001  the write succeeded; a stale cache entry expires with its TTL
        logger.warning("answer cache could not be cleared after a document change", exc_info=True)
        ingestion_jobs._metric("admin_best_effort_failed_total", "answer_cache_clear")


class ConversationRedactionError(RuntimeError):
    """The conversation privacy boundary could not be established."""


def _redact_conversations(tenant: str, docs: Sequence[str]) -> int:
    from webapi import production_guard

    if not _env_flag("CONVERSATION_REDACT_ON_DELETE", True) and not production_guard.is_production():
        return 0
    try:
        from webapi import conversation_store

        redact = conversation_store.redact_source_doc
        return sum(int(redact(tenant, doc) or 0) for doc in docs)
    except Exception as exc:  # noqa: BLE001  fail closed without exposing the underlying error
        raise ConversationRedactionError("conversation redaction failed") from exc


@contextmanager
def _maintenance() -> Iterator[None]:
    with ingestion_jobs.maintenance_shared():
        yield


@contextmanager
def _served_locked(expected: str) -> Iterator[None]:
    """The served collection's write lock; refuses when another generation was activated meanwhile."""
    with ingestion_jobs._collection_write_lock(expected):
        if _served_name() != expected:
            raise RuntimeError("the served generation changed while waiting for its lock; retry")
        yield


def _remaining(store, keys: Iterable[Tuple[str, str]]) -> int:
    return sum(len(_doc_ids(store, tenant, doc)) for tenant, doc in keys)


def _purge(store, keyword_path: str, keys: Sequence[Tuple[str, str]]) -> Dict[str, int]:
    """Remove documents from one collection: keyword rows, then vector rows, then check none is left.

    The caller has written the tombstones first, so a crash between the steps converges on the next run."""
    from scripts.ingest_canonical_jsonl import remove_keyword_documents

    if not keys:
        return {"keyword_rows_removed": 0, "chunks_deleted": 0}
    keyword = remove_keyword_documents(keyword_path, list(keys))
    deleted = 0
    for tenant, doc in keys:
        ids = _doc_ids(store, tenant, doc)
        for part in _chunks(ids, 1000):
            store.delete(ids=list(part))
            deleted += len(part)
    left = _remaining(store, keys)
    if left:
        raise RuntimeError(f"deletion incomplete: {left} row(s) of deleted documents remain")
    return {"keyword_rows_removed": int(keyword.get("removed", 0)), "chunks_deleted": deleted}


def delete_documents(collection: Optional[str], *, tenant: str, source_docs: Sequence[str],
                     execute: bool = False, reason: str = "admin_delete", tombstone: bool = True) -> Dict[str, Any]:
    """collection None = the served collection.

    execute=True: tombstones first (unless tombstone=False), the operation record, the manifest marked stale,
    keyword rows then vector rows removed and checked, cached answers cleared, conversation answers that cited
    the documents redacted."""
    tenant = str(tenant or "").strip() or "default"
    docs = sorted({str(d).strip() for d in source_docs if str(d).strip()})
    if not docs:
        raise ValueError("source_docs is required")
    target = None if collection in (None, "") or ingestion_jobs.is_production_collection(collection) else collection
    if target is not None:
        _staging_owned(target, tenant)
    lock_name = target or _served_name()
    with ExitStack() as stack:
        if execute:
            stack.enter_context(_maintenance())
        if target is None:
            stack.enter_context(_served_locked(lock_name))
        else:
            stack.enter_context(ingestion_jobs._collection_write_lock(lock_name))
        keyword_path = _keyword_path(target)  # resolved once, under the lock (an activation cannot move it now)
        vectors = _open(target)
        found: Dict[str, List[str]] = {doc: _doc_ids(vectors, tenant, doc) for doc in docs}
        report = {
            "collection": target or "(served)", "tenant": tenant, "executed": bool(execute),
            "documents": {doc: len(ids) for doc, ids in found.items()},
            "chunks": sum(len(ids) for ids in found.values()),
        }
        if not execute:
            return report
        if tombstone:
            ingestion_jobs.add_tombstones(tenant, docs, reason=reason)
        op = ingestion_jobs.start_operation("deletion", collection=lock_name, tenant=tenant, documents=len(docs),
                                            chunks=report["chunks"], reason=reason)
        try:
            report["conversation_turns_redacted"] = _redact_conversations(tenant, docs)
            _mark_stale(lock_name)  # before the first write: a crash leaves a stale (unverified) manifest
            purged = _purge(vectors, keyword_path, [(tenant, doc) for doc in docs])
            report["keyword_rows_removed"] = purged["keyword_rows_removed"]
            report["tombstoned"] = len(docs) if tombstone else 0
            ingestion_jobs.finish_operation(op, ingestion_jobs.OP_DONE, chunks_deleted=purged["chunks_deleted"],
                                            keyword_rows_removed=purged["keyword_rows_removed"])
        except Exception as exc:  # noqa: BLE001  records the operation as failed, then re-raises
            ingestion_jobs.finish_operation(op, ingestion_jobs.OP_FAILED, error=type(exc).__name__)
            raise
        report["operation_id"] = op["op_id"]
    _clear_answer_cache(tenant)
    return report


def _rows(vectors, ids: Sequence[str]) -> Tuple[List[str], Any, List[str], List[Dict[str, Any]]]:
    res = vectors.get(ids=list(ids), include=["embeddings", "documents", "metadatas"])
    embeddings = res.get("embeddings")
    embeddings = _as_vectors(embeddings if embeddings is not None else [])
    _MAX_STATS["buffered_rows"] = max(_MAX_STATS["buffered_rows"], len(embeddings))
    return list(res.get("ids") or []), embeddings, list(res.get("documents") or []), list(res.get("metadatas") or [])


def _as_vectors(embeddings: Any) -> Any:
    """float32 rows (a quarter of the memory of Python float lists); plain lists without numpy."""
    try:
        import numpy as np

        if len(embeddings) == 0:
            return []
        return [row for row in np.asarray(embeddings, dtype=np.float32)]
    except ImportError:  # pragma: no cover - numpy is a dependency of both vector backends
        return [list(map(float, e)) for e in embeddings]


def _present(value: Any) -> bool:
    return value is not None and not (isinstance(value, str) and not value.strip())


def _general_doc(doc: str) -> bool:
    return doc.casefold().endswith(_GENERAL_DOC_EXTENSIONS)


def _typed_or_allowed_legacy(row: Dict[str, Any], doc: str) -> bool:
    if any(_present(row.get(k)) for k in _TYPE_FIELDS):
        return True
    return _env_flag("PROMOTION_ALLOW_UNTYPED") and _general_doc(doc)


def _check_vector_meta(chunk_id: Any, meta: Any, tenant: str) -> str:
    """The document name of one staging vector row, or ValueError (fail-closed)."""
    if not isinstance(meta, dict) or not isinstance(chunk_id, str) or not chunk_id.strip():
        raise ValueError("promotion refused: incomplete staging metadata")
    if is_private_mail_record(meta, chunk_id):
        raise ValueError("promotion refused: private mail is quarantined")
    # A field that is present but None / "" is treated like a missing one; any other non-string value refuses.
    if any(_present(meta.get(k)) and not isinstance(meta[k], str) for k in _STRING_FIELDS):
        raise ValueError("promotion refused: incomplete staging metadata")
    row_tenant = meta.get("tenant_id") if _present(meta.get("tenant_id")) else None
    if row_tenant is None and tenant != "default":
        raise ValueError("promotion refused: inconsistent staging metadata")
    doc = str((meta.get("source_doc") if _present(meta.get("source_doc")) else meta.get("doc_id")) or "").strip()
    if (row_tenant not in (None, tenant) or (_present(meta.get("id")) and meta["id"] != chunk_id)
            or not _typed_or_allowed_legacy(meta, doc)):
        raise ValueError("promotion refused: inconsistent staging metadata")
    if not doc:
        raise ValueError("promotion refused: incomplete staging metadata")
    return doc


def _child_ids(row: Dict[str, Any]) -> List[str]:
    raw = row.get("child_chunk_ids")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    return [str(x) for x in raw] if isinstance(raw, list) else []


def _check_keyword_row(row: Dict[str, Any], tenant: str, doc_ids: Dict[Tuple[str, str], Set[str]]) -> str:
    """The id of one staging keyword row, or ValueError. Parent rows (INGEST_EMBED_PARENT_CHUNKS=false keeps them
    out of the vector store) pass when every child they name is a promoted vector row of the same document."""
    unsafe = ValueError("promotion refused: unsafe staging keyword records")
    if is_private_mail_record(row):
        raise unsafe
    row_tenant = row.get("tenant_id") if _present(row.get("tenant_id")) else None
    if row_tenant is None and tenant != "default":
        raise unsafe
    if row_tenant not in (None, tenant):
        raise unsafe
    if any(_present(row.get(k)) and not isinstance(row[k], str) for k in _STRING_FIELDS):
        raise unsafe
    doc = (row.get("source_doc") if _present(row.get("source_doc")) else row.get("doc_id"))
    chunk_id = row.get("id")
    if not isinstance(doc, str) or not doc.strip() or not isinstance(chunk_id, str) or not chunk_id.strip():
        raise unsafe
    if not _typed_or_allowed_legacy(row, doc):
        raise unsafe
    ids = doc_ids.get((tenant, doc.strip()), set())
    if chunk_id in ids:
        return chunk_id
    if str(row.get("chunk_role") or "").strip().lower() == "parent":
        children = _child_ids(row)
        if children and all(child in ids for child in children):
            return chunk_id
    raise unsafe


def promote_staging(collection: str, *, tenant: str, execute: bool = False) -> Dict[str, Any]:
    """Copy the staging collection's documents of `tenant` into the served collection."""
    tenant = str(tenant or "").strip() or "default"
    if ingestion_jobs.is_production_collection(collection):
        raise ValueError("refusing to promote the production collection into itself")
    _staging_owned(collection, tenant, ready=True)
    with ingestion_jobs._collection_write_lock(collection):  # no job rewrites it meanwhile
        return _promote_locked(collection, tenant, execute)


def _validate_staging(source, collection: str, tenant: str) -> Tuple[List[str], Dict[Tuple[str, str], Set[str]], Set[str]]:
    """Pass 1 (metadata only): (all ids, document -> ids, keyword row ids). Raises before anything is written."""
    from rag_core.index_generation import keyword_mail_rows
    from scripts.ingest_canonical_jsonl import iter_keyword_documents

    # The default tenant also owns legacy rows that have no tenant_id at all: look at every row and keep the ones
    # that are the tenant's (rows of another tenant are left alone, never promoted).
    legacy_default = tenant == "default"
    where = None if legacy_default else {"tenant_id": tenant}
    candidate_ids = [str(i) for i in (source.get(where=where, include=[]).get("ids") or [])]
    if not candidate_ids:
        raise ValueError("the staging collection has no rows of this tenant")
    doc_ids: Dict[Tuple[str, str], Set[str]] = {}
    all_ids: List[str] = []
    selected = set(candidate_ids)
    scan_ids = candidate_ids if legacy_default else list(source.get(include=[]).get("ids") or [])
    if not selected.issubset(scan_ids) or len(set(scan_ids)) != len(scan_ids):
        raise ValueError("promotion refused: incomplete staging records")
    for part in _chunks(scan_ids, _BATCH):
        res = source.get(ids=list(part), include=["metadatas"])
        ids, metadatas = list(res.get("ids") or []), list(res.get("metadatas") or [])
        if len(ids) != len(part) or set(ids) != set(part) or len(set(ids)) != len(ids) or len(metadatas) != len(ids):
            raise ValueError("promotion refused: incomplete staging records")
        for chunk_id, meta in zip(ids, metadatas):
            if is_private_mail_record(meta, chunk_id):
                raise ValueError("promotion refused: private mail is quarantined")
            if not isinstance(meta, dict):
                raise ValueError("promotion refused: incomplete staging metadata")
            if chunk_id not in selected:
                continue
            if legacy_default and _present(meta.get("tenant_id")) \
                    and meta.get("tenant_id") != tenant:
                continue
            all_ids.append(chunk_id)
            doc = _check_vector_meta(chunk_id, meta, tenant)
            doc_ids.setdefault((tenant, doc), set()).add(chunk_id)
    if not all_ids:
        raise ValueError("the staging collection has no rows of this tenant")
    keyword_ids: Set[str] = set()
    if keyword_mail_rows(_keyword_path(collection)):
        raise ValueError("promotion refused: unsafe staging keyword records")
    for row in iter_keyword_documents(_keyword_path(collection), list(doc_ids.keys())):
        keyword_ids.add(_check_keyword_row(row, tenant, doc_ids))
    return all_ids, doc_ids, keyword_ids


def _promote_locked(collection: str, tenant: str, execute: bool) -> Dict[str, Any]:
    from scripts.ingest_canonical_jsonl import iter_keyword_documents, new_generation_id, write_keyword_rows

    source = _open(collection)
    all_ids, doc_ids, keyword_ids = _validate_staging(source, collection, tenant)
    tombstoned = ingestion_jobs.tombstoned_keys(tenant)
    skipped = sorted(key for key in doc_ids if key in tombstoned)
    skipped_ids: Set[str] = set().union(*(doc_ids[key] for key in skipped)) if skipped else set()
    kept = {key: ids for key, ids in doc_ids.items() if key not in tombstoned}
    promote_ids = [i for i in all_ids if i not in skipped_ids]
    report: Dict[str, Any] = {
        "collection": collection, "tenant": tenant, "executed": bool(execute),
        "documents": len(doc_ids), "chunks": len(all_ids),
        "skipped_tombstoned": [doc for _, doc in skipped],
    }
    served = _served_name()
    with ExitStack() as stack:
        if execute:
            stack.enter_context(_maintenance())
        stack.enter_context(_served_locked(served))
        served_keyword = _keyword_path(None)  # resolved once, under the served lock
        target = _open(None)
        replaced = 0
        for (doc_tenant, doc), ids in kept.items():
            replaced += len(set(_doc_ids(target, doc_tenant, doc)) - ids)
        purge_keys = _present_tombstones(target, served_keyword, sorted(tombstoned))
        report["served_chunks_replaced"] = replaced
        report["served_documents_tombstoned"] = [doc for _, doc in purge_keys]
        if not execute:
            return report
        generation = new_generation_id()
        op = ingestion_jobs.start_operation(
            "promotion", staging=collection, served=served, tenant=tenant, documents=len(kept),
            chunks=len(promote_ids), purge_documents=len(purge_keys), index_generation=generation,
        )
        try:
            _redact_conversations(tenant, [doc for _, doc in sorted(tombstoned)])
            _mark_stale(served)  # before the first write: a crash leaves a stale (unverified) manifest
            purged = _purge(target, served_keyword, purge_keys)
            for part in _chunks(promote_ids, _BATCH):
                ids, embeddings, documents, metadatas = _rows(source, part)
                if (len(ids) != len(part) or set(ids) != set(part) or len(embeddings) != len(ids)
                        or len(documents) != len(ids) or len(metadatas) != len(ids)):
                    raise ValueError("promotion refused: incomplete staging records")
                for chunk_id, meta in zip(ids, metadatas):
                    _check_vector_meta(chunk_id, meta, tenant)
                    meta["index_generation"] = generation
                target.upsert(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)
            report["served_chunks_deleted"] = _delete_stale(target, kept)
            rows = (row for row in iter_keyword_documents(_keyword_path(collection), list(kept.keys()))
                    if _check_keyword_row(row, tenant, kept))
            keyword = write_keyword_rows(served_keyword, rows, generation_id=generation,
                                         replaced_keys=set(kept.keys()), new_ids=set(keyword_ids) - skipped_ids)
            report.update({"keyword_rows_written": keyword["written"], "index_generation": generation,
                           "served_tombstone_chunks_deleted": purged["chunks_deleted"]})
            _mark_stale(served)
            ingestion_jobs.finish_operation(op, ingestion_jobs.OP_DONE, chunks_written=len(promote_ids),
                                            keyword_rows_written=keyword["written"])
        except Exception as exc:  # noqa: BLE001  records the operation as failed, then re-raises
            ingestion_jobs.finish_operation(op, ingestion_jobs.OP_FAILED, error=type(exc).__name__)
            raise
        report["operation_id"] = op["op_id"]
    _clear_answer_cache(tenant)
    return report


def _delete_stale(target, kept: Dict[Tuple[str, str], Set[str]]) -> int:
    """Rows of the promoted documents that the new version does not have (legacy doc_id-only rows included)."""
    deleted = 0
    for (doc_tenant, doc), ids in kept.items():
        stale = [i for i in _doc_ids(target, doc_tenant, doc) if i not in ids]
        for part in _chunks(stale, 1000):
            target.delete(ids=list(part))
            deleted += len(part)
    return deleted


def _present_tombstones(vectors, keyword_path: str, keys: Sequence[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """A deleted document may survive only in the keyword corpus."""
    from scripts.ingest_canonical_jsonl import iter_keyword_documents

    if not keys:
        return []
    present = {key for key in keys if _doc_ids(vectors, *key)}
    if os.path.exists(keyword_path):
        present.update((str(row.get("tenant_id") or "").strip() or "default",
                        str(row.get("source_doc") or row.get("doc_id") or "").strip())
                       for row in iter_keyword_documents(keyword_path, keys))
    return sorted(present)


def purge_tombstoned(collection: str, keyword_path: str, *, tenant: Optional[str] = None) -> Dict[str, Any]:
    """Remove every tombstoned document from one collection (rag_core.index_generation calls this, under the
    collection's lock, before a generation is activated or rolled back to)."""
    from rag_core import store

    keys = sorted(ingestion_jobs.tombstoned_keys(tenant))
    if not keys:
        return {"documents": 0, "chunks_deleted": 0, "keyword_rows_removed": 0}
    vectors = store.get_vectorstore(collection_name=collection, create_if_missing=False,
                                    verify_embedding_fingerprint=False)
    todo = _present_tombstones(vectors, keyword_path, keys)
    with ingestion_jobs.maintenance_shared():
        op = ingestion_jobs.start_operation("deletion", collection=collection, tenant=tenant or "*",
                                            documents=len(todo), reason="tombstone_reapply")
        try:
            for tenant_id in sorted({t for t, _ in keys}):
                _redact_conversations(tenant_id, [doc for t, doc in keys if t == tenant_id])
            if todo:
                _mark_stale(collection)
            purged = _purge(vectors, keyword_path, todo)
            ingestion_jobs.finish_operation(op, ingestion_jobs.OP_DONE, **purged)
        except Exception as exc:  # noqa: BLE001  records the operation as failed, then re-raises
            ingestion_jobs.finish_operation(op, ingestion_jobs.OP_FAILED, error=type(exc).__name__)
            raise
    for tenant_id in sorted({t for t, _ in todo}):
        _clear_answer_cache(tenant_id)
    return {"documents": len(todo), **purged}
