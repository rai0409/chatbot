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
import logging
import os
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import config
from rag_core import embedder, store
from rag_core.embedding_fingerprint import stamp_collection_metadata
from rag_core.index_generation import fsync_dir
from rag_core.ja_text import active_normalization_version
from rag_core.utils import ensure_openai_client

_ENABLED_VALUES = {"1", "true", "yes", "y", "on"}  # the set of config._get_bool


def _iter_jsonl(path: str) -> Iterable[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                yield {"__parse_error__": True}
                continue
            if isinstance(obj, dict):
                yield obj
            else:
                yield {"__parse_error__": True}


def _chunked(items: List[str], size: int) -> Iterable[List[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _sanitize_metadata_value(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _sanitize_metadata(meta: Dict[str, Any]) -> Dict[str, Any]:
    return {key: _sanitize_metadata_value(value) for key, value in meta.items()}


def _numeric_source_pages(value: Any) -> List[int]:
    if not isinstance(value, list):
        return []
    pages: List[int] = []
    for item in value:
        if isinstance(item, bool):
            continue
        try:
            pages.append(int(item))
        except (TypeError, ValueError):
            continue
    return pages


def _get_collection_client(collection) -> Optional[Any]:
    return getattr(collection, "_client", None) or getattr(collection, "client", None)


def _reset_collection(collection) -> Any:
    name = getattr(collection, "name", None)
    client = _get_collection_client(collection)
    if client is not None and name:
        try:
            client.delete_collection(name=name)
            return store.get_vectorstore(
                collection_name=name, verify_embedding_fingerprint=False
            )
        except Exception:
            pass
    try:
        res = collection.get(include=[])
    except Exception:
        try:
            res = collection.get()
        except Exception:
            return collection
    ids = res.get("ids") or []
    for ids_chunk in _chunked(list(ids), 1000):
        if not ids_chunk:
            continue
        try:
            collection.delete(ids=ids_chunk)
        except Exception:
            continue
    return collection


def _env_flag(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if raw == "":
        return default
    return raw in _ENABLED_VALUES


def _doc_key(row: Dict[str, Any]) -> Tuple[str, str]:
    tenant = str(row.get("tenant_id") or "").strip() or "default"
    source = str(row.get("source_doc") or row.get("doc_id") or "").strip()
    return tenant, source


def _embedding_text(row: Dict[str, Any], text: str) -> str:
    # The vector is built from the same text the keyword index scores
    # (title + section path + chunk body); the stored document stays the
    # display text so citations quote the original.
    searchable = str(row.get("searchable_text") or "").strip()
    return searchable or text


def new_generation_id() -> str:
    return uuid.uuid4().hex


def document_row_ids(collection, tenant: str, source: str) -> List[str]:
    """Every row id of the document (tenant, source) under the _doc_key rule.

    Rows are matched by tenant_id + source_doc, and also (legacy rows) by doc_id when source_doc is empty, and
    without tenant_id for the default tenant, so a document indexed before source_doc / tenant_id existed is
    replaced or deleted instead of staying behind as an orphan."""
    tenant = str(tenant or "").strip() or "default"
    source = str(source or "").strip()
    if not source:
        return []
    ids: List[str] = []
    seen: Set[str] = set()

    def take(found: Iterable[str]) -> None:
        for chunk_id in found:
            if chunk_id not in seen:
                seen.add(chunk_id)
                ids.append(chunk_id)

    res = collection.get(where={"$and": [{"tenant_id": tenant}, {"source_doc": source}]}, include=[])
    take(str(i) for i in (res.get("ids") or []))
    fallbacks = [{"doc_id": source}]
    if tenant == "default":
        fallbacks.append({"source_doc": source})
    for where in fallbacks:
        try:
            res = collection.get(where=where, include=["metadatas"])
        except Exception:  # noqa: BLE001 - a backend without the field index: the primary match stands
            # Legacy rows matched only by this clause would stay behind after a delete/replace: say so.
            logging.getLogger(__name__).warning("legacy row lookup %s failed; only the primary match is used",
                                                sorted(where), exc_info=True)
            continue
        metas = res.get("metadatas") or []
        take(str(i) for i, meta in zip(res.get("ids") or [], metas) if _doc_key(dict(meta or {})) == (tenant, source))
    return ids


def _delete_stale_document_rows(collection, doc_keys: Dict[Tuple[str, str], Set[str]]) -> int:
    # Runs after the new rows are upserted, so a document is never missing
    # from search while it is being replaced.
    deleted = 0
    for (tenant, source), new_ids in doc_keys.items():
        if not source:
            continue
        stale = [i for i in document_row_ids(collection, tenant, source) if i not in new_ids]
        for ids_chunk in _chunked(stale, 1000):
            collection.delete(ids=ids_chunk)
            deleted += len(ids_chunk)
    return deleted


def _assert_no_cross_tenant_id_collision(collection, ids, metas) -> None:
    """Refuse a batch whose ids already belong to another tenant (ids are collection-wide)."""
    getter = getattr(collection, "get", None)
    if not ids or getter is None:
        return
    existing = getter(ids=list(ids), include=["metadatas"])
    found = dict(zip(existing.get("ids") or [], existing.get("metadatas") or []))
    clashes = []
    for chunk_id, meta in zip(ids, metas):
        old = found.get(chunk_id)
        if old is None:
            continue
        old_tenant = str((old or {}).get("tenant_id") or "").strip() or "default"
        new_tenant = str((meta or {}).get("tenant_id") or "").strip() or "default"
        if old_tenant != new_tenant:
            clashes.append(chunk_id)
    if clashes:
        raise ValueError(
            f"{len(clashes)} chunk id(s) already belong to another tenant (e.g. {clashes[:5]}); refusing to overwrite"
        )


def ingest_canonical_rows(
    rows: Iterable[Dict[str, Any]],
    *,
    collection_name: Optional[str] = None,
    batch: int = 64,
    reset: bool = False,
    source_jsonl_path: Optional[str] = None,
    client=None,
    replace_documents: bool = True,
    embed_parents: Optional[bool] = None,
    generation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Embed and upsert canonical rows.

    replace_documents: rows of the same (tenant_id, source_doc) that are not
      in this batch are deleted afterwards, so an updated document leaves no
      stale chunks behind.
    embed_parents: parent chunks are only used for context expansion (read
      from the keyword JSONL); INGEST_EMBED_PARENT_CHUNKS=false skips their
      vectors. Default keeps the historical behaviour (embed everything).
    generation_id: stamped on every row as index_generation; pass the same id
      to write_keyword_rows so both indexes can be checked for consistency.
    """
    if client is None and not embedder.is_local_provider():
        client = ensure_openai_client(base_url=config.OPENAI_BASE_URL)
    if embed_parents is None:
        embed_parents = _env_flag("INGEST_EMBED_PARENT_CHUNKS", True)
    generation = generation_id or new_generation_id()

    collection = store.get_vectorstore(
        collection_name=collection_name, verify_embedding_fingerprint=False
    )
    if reset:
        collection = _reset_collection(collection)
    fingerprint = store.stamp_collection_fingerprint(collection)
    store.stamp_collection_profile(collection)

    batch_ids: List[str] = []
    batch_texts: List[str] = []
    batch_embed_texts: List[str] = []
    batch_metas: List[Dict[str, Any]] = []
    ingested = 0
    dim_checked = False
    skipped = 0
    parents_not_embedded = 0
    doc_ids: Dict[Tuple[str, str], Set[str]] = {}

    def flush_batch() -> None:
        nonlocal ingested, dim_checked
        if not batch_ids:
            return
        _assert_no_cross_tenant_id_collision(collection, batch_ids, batch_metas)
        embeddings = embedder.embed_documents(batch_embed_texts, client=client)
        if not dim_checked:
            store.check_and_stamp_embedding_dim(collection, len(embeddings[0]))
            dim_checked = True
        if hasattr(collection, "upsert"):
            collection.upsert(
                ids=batch_ids,
                documents=batch_texts,
                embeddings=embeddings,
                metadatas=batch_metas,
            )
        else:
            try:
                collection.delete(ids=batch_ids)
            except Exception:
                pass
            collection.add(
                ids=batch_ids,
                documents=batch_texts,
                embeddings=embeddings,
                metadatas=batch_metas,
            )
        ingested += len(batch_ids)
        batch_ids.clear()
        batch_texts.clear()
        batch_embed_texts.clear()
        batch_metas.clear()

    for row in rows:
        if row.get("__parse_error__"):
            skipped += 1
            continue
        raw_id = row.get("id")
        text = row.get("text")
        if raw_id is None or str(raw_id).strip() == "":
            skipped += 1
            continue
        if text is None or str(text).strip() == "":
            skipped += 1
            continue
        chunk_id = str(raw_id)
        doc_ids.setdefault(_doc_key(row), set()).add(chunk_id)
        if not embed_parents and str(row.get("chunk_role") or "").strip().lower() == "parent":
            parents_not_embedded += 1
            continue
        meta = dict(row)
        meta["id"] = chunk_id
        meta.pop("text", None)
        meta["tenant_id"] = str(meta.get("tenant_id") or "").strip() or "default"
        meta["index_generation"] = generation
        meta.setdefault("normalization_version", active_normalization_version())
        if not meta.get("source_pages"):
            meta["source_pages"] = [-1]
            meta.setdefault("searchable", 1)
        if meta.get("searchable") is None:
            meta["searchable"] = 1
        source_pages = _numeric_source_pages(meta.get("source_pages"))
        if source_pages:
            meta["source_page_start"] = source_pages[0]
            meta["source_page_end"] = source_pages[-1]
        meta = _sanitize_metadata(meta)

        batch_ids.append(chunk_id)
        batch_texts.append(str(text))
        batch_embed_texts.append(_embedding_text(row, str(text)))
        batch_metas.append(meta)

        if len(batch_ids) >= batch:
            flush_batch()

    if batch_ids:
        flush_batch()

    stale_deleted = 0
    if replace_documents and not reset and doc_ids:
        stale_deleted = _delete_stale_document_rows(collection, doc_ids)

    if source_jsonl_path:
        fingerprint = stamp_collection_metadata(
            collection,
            source_jsonl_path=source_jsonl_path,
            chunk_count=ingested,
        )

    return {
        "collection": str(getattr(collection, "name", "") or collection_name or ""),
        "embedding_fingerprint": fingerprint,
        "ingested": ingested,
        "skipped": skipped,
        "parents_not_embedded": parents_not_embedded,
        "stale_deleted": stale_deleted,
        "documents": len(doc_ids),
        "index_generation": generation,
    }


def _keyword_row(r: Dict[str, Any], generation_id: Optional[str]) -> Dict[str, Any]:
    r = dict(r)
    if generation_id:
        r["index_generation"] = generation_id
    # The keyword index tokenizes with the active normalization; a later
    # change of NORMALIZATION_PROFILE is detected from this field.
    r.setdefault("normalization_version", active_normalization_version())
    return r


def write_keyword_rows(
    path: str | Path,
    rows: Iterable[Dict[str, Any]],
    *,
    replace_documents: bool = True,
    generation_id: Optional[str] = None,
    replaced_keys: Optional[Set[Tuple[str, str]]] = None,
    new_ids: Optional[Set[str]] = None,
    allow_unparseable: Optional[bool] = None,
) -> Dict[str, Any]:
    """Merge rows into a keyword-index JSONL and replace it atomically.

    Existing rows of the documents being written are dropped first (document
    level replacement), other documents are kept. The file is written to a
    temporary name and renamed, so readers never see a partial file.

    replaced_keys + new_ids (both given): `rows` is consumed lazily (a generator), so a large promotion never
    holds its keyword rows in memory; the caller already knows the documents and ids it writes.
    """
    target = Path(path)
    if replaced_keys is not None and new_ids is not None:
        replaced = set(replaced_keys) if replace_documents else set()
        ids = set(new_ids)
        counter = {"written": 0}

        def stream() -> Iterable[Dict[str, Any]]:
            for r in rows:
                if r.get("__parse_error__") or not str(r.get("id") or "").strip():
                    continue
                counter["written"] += 1
                yield _keyword_row(r, generation_id)

        kept, dropped = _rewrite_keyword_corpus(
            target, lambda old: str(old.get("id") or "") in ids or _doc_key(old) in replaced, stream(),
            allow_unparseable=allow_unparseable,
        )
        return {"path": str(target), "written": counter["written"], "kept": kept, "replaced": dropped}
    new_rows = [_keyword_row(r, generation_id) for r in rows if not r.get("__parse_error__") and str(r.get("id") or "").strip()]
    replaced = {_doc_key(r) for r in new_rows} if replace_documents else set()
    ids = {str(r["id"]) for r in new_rows}
    kept, dropped = _rewrite_keyword_corpus(
        target, lambda old: str(old.get("id") or "") in ids or _doc_key(old) in replaced, new_rows,
        allow_unparseable=allow_unparseable,
    )
    return {"path": str(target), "written": len(new_rows), "kept": kept, "replaced": dropped}


def remove_keyword_documents(path: str | Path, doc_keys: Iterable[Tuple[str, str]],
                             allow_unparseable: Optional[bool] = None) -> Dict[str, Any]:
    """Drop every row of these (tenant_id, source_doc) documents (stage 15: document deletion)."""
    keys = set(doc_keys)
    if not Path(path).exists():
        return {"path": str(path), "kept": 0, "removed": 0}
    kept, removed = _rewrite_keyword_corpus(Path(path), lambda old: _doc_key(old) in keys, [],
                                            allow_unparseable=allow_unparseable)
    return {"path": str(path), "kept": kept, "removed": removed}


def iter_keyword_documents(path: str | Path, doc_keys: Iterable[Tuple[str, str]]) -> Iterable[Dict[str, Any]]:
    """Rows of these documents, one at a time (promotion streams them)."""
    keys = set(doc_keys)
    if not Path(path).exists():
        return
    for row in _iter_jsonl(str(path)):
        if not row.get("__parse_error__") and _doc_key(row) in keys:
            yield row


def read_keyword_documents(path: str | Path, doc_keys: Iterable[Tuple[str, str]]) -> List[Dict[str, Any]]:
    """Rows of these documents (stage 15: promotion copies them)."""
    return list(iter_keyword_documents(path, doc_keys))


class KeywordCorpusError(RuntimeError):
    """The keyword corpus has lines that are not JSON objects; rewriting it would silently drop them."""


def _rewrite_keyword_corpus(target: Path, drop, new_rows: Iterable[Dict[str, Any]],
                            allow_unparseable: Optional[bool] = None) -> Tuple[int, int]:
    """(kept, dropped). Stage 14: one writer at a time (a sync and an async
    job, several workers), a temp name no other writer uses, and kept rows
    streamed line by line instead of holding the whole corpus in memory.

    Lines that are not JSON objects used to vanish silently on every rewrite: they are counted and the rewrite is
    refused (KeywordCorpusError) unless allow_unparseable / KEYWORD_REWRITE_ALLOW_UNPARSEABLE=true (then they are
    dropped and logged). The new file is fsynced, renamed, and its directory fsynced."""
    if allow_unparseable is None:
        allow_unparseable = _env_flag("KEYWORD_REWRITE_ALLOW_UNPARSEABLE", False)
    kept = 0
    dropped = 0
    unparseable = 0
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    with _exclusive(target.with_name(f".{target.name}.lock")):
        try:
            with tmp.open("w", encoding="utf-8") as out:
                if target.exists():
                    with target.open("r", encoding="utf-8") as src:
                        for line in src:
                            if not line.strip():
                                continue
                            try:
                                old = json.loads(line)
                            except ValueError:
                                unparseable += 1
                                continue
                            if not isinstance(old, dict):
                                unparseable += 1
                                continue
                            if drop(old):
                                dropped += 1
                                continue
                            out.write(line if line.endswith("\n") else line + "\n")
                            kept += 1
                if unparseable and not allow_unparseable:
                    raise KeywordCorpusError(
                        f"{target.name}: {unparseable} line(s) are not JSON objects; refusing to rewrite (fix the file "
                        "or set KEYWORD_REWRITE_ALLOW_UNPARSEABLE=true to drop them)"
                    )
                for r in new_rows:
                    out.write(json.dumps(r, ensure_ascii=False) + "\n")
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, target)
            fsync_dir(target.parent)
        finally:
            if tmp.exists():
                tmp.unlink()
    if unparseable:
        print(f"warning: dropped {unparseable} unparseable line(s) from {target.name}", file=sys.stderr)
    return kept, dropped


@contextmanager
def _exclusive(lock_path: Path):
    import fcntl

    with open(lock_path, "a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _production_mail_refusal(collection: Optional[str], rows: List[Dict[str, Any]]) -> str:
    """Private mail never goes into the served (production) collection through this script (fail-closed)."""
    from rag_core.mail_access import is_private_mail_record

    try:
        from webapi.ingestion_jobs import is_production_collection

        production = is_production_collection(collection)
    except Exception:  # noqa: BLE001 - cannot tell: treat as production
        production = True
    if not production:
        return ""
    mail = sum(1 for row in rows if not row.get("__parse_error__") and is_private_mail_record(row))
    if mail:
        return f"refusing {mail} private mail row(s) for the production collection (mail is quarantined)"
    return ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("canonical_jsonl_path")
    parser.add_argument("--batch", type=int, default=64)
    parser.add_argument("--collection", default=None)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument(
        "--no-replace-documents",
        action="store_true",
        help="keep rows of re-ingested documents that are absent from the input",
    )
    parser.add_argument(
        "--keyword-index",
        default=None,
        help="also merge the rows into this keyword-index JSONL with the same index_generation",
    )
    args = parser.parse_args()

    rows = list(_iter_jsonl(args.canonical_jsonl_path))
    refusal = _production_mail_refusal(args.collection, rows)
    if refusal:
        print(f"error: {refusal}", file=sys.stderr)
        return 1
    generation = new_generation_id()
    try:
        result = ingest_canonical_rows(
            rows,
            collection_name=args.collection,
            batch=args.batch,
            reset=args.reset,
            source_jsonl_path=args.canonical_jsonl_path,
            replace_documents=not args.no_replace_documents,
            generation_id=generation,
        )
    except Exception as exc:
        print(f"error: failed to upsert batch: {exc}", file=sys.stderr)
        return 1
    if args.keyword_index:
        keyword = write_keyword_rows(
            args.keyword_index,
            rows,
            replace_documents=not args.no_replace_documents,
            generation_id=generation,
        )
        print(f"keyword_index={json.dumps(keyword, ensure_ascii=False)}")

    print(f"embedding_fingerprint={json.dumps(result['embedding_fingerprint'], ensure_ascii=False)}")
    print(
        f"done ingested={result['ingested']} skipped={result['skipped']} "
        f"stale_deleted={result['stale_deleted']} parents_not_embedded={result['parents_not_embedded']} "
        f"index_generation={result['index_generation']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
