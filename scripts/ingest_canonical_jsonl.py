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
import os
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import config
from rag_core import embedder, store
from rag_core.embedding_fingerprint import stamp_collection_metadata
from rag_core.ja_text import active_normalization_version
from rag_core.utils import ensure_openai_client

_ENABLED_VALUES = {"1", "true", "yes", "on"}


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


def _delete_stale_document_rows(collection, doc_keys: Dict[Tuple[str, str], Set[str]]) -> int:
    # Runs after the new rows are upserted, so a document is never missing
    # from search while it is being replaced.
    deleted = 0
    for (tenant, source), new_ids in doc_keys.items():
        if not source:
            continue
        where = {"$and": [{"tenant_id": tenant}, {"source_doc": source}]}
        res = collection.get(where=where, include=[])
        stale = [i for i in (res.get("ids") or []) if i not in new_ids]
        for ids_chunk in _chunked(stale, 1000):
            collection.delete(ids=ids_chunk, where=where)
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


def write_keyword_rows(
    path: str | Path,
    rows: Iterable[Dict[str, Any]],
    *,
    replace_documents: bool = True,
    generation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Merge rows into a keyword-index JSONL and replace it atomically.

    Existing rows of the documents being written are dropped first (document
    level replacement), other documents are kept. The file is written to a
    temporary name and renamed, so readers never see a partial file.
    """
    target = Path(path)
    new_rows = [dict(r) for r in rows if not r.get("__parse_error__") and str(r.get("id") or "").strip()]
    for r in new_rows:
        if generation_id:
            r["index_generation"] = generation_id
        # The keyword index tokenizes with the active normalization; a later
        # change of NORMALIZATION_PROFILE is detected from this field.
        r.setdefault("normalization_version", active_normalization_version())
    replaced = {_doc_key(r) for r in new_rows} if replace_documents else set()
    new_ids = {str(r["id"]) for r in new_rows}
    kept, dropped = _rewrite_keyword_corpus(
        Path(path), lambda old: str(old.get("id") or "") in new_ids or _doc_key(old) in replaced, new_rows
    )
    return {"path": str(target), "written": len(new_rows), "kept": kept, "replaced": dropped}


def remove_keyword_documents(path: str | Path, doc_keys: Iterable[Tuple[str, str]]) -> Dict[str, Any]:
    """Drop every row of these (tenant_id, source_doc) documents (stage 15: document deletion)."""
    keys = set(doc_keys)
    if not Path(path).exists():
        return {"path": str(path), "kept": 0, "removed": 0}
    kept, removed = _rewrite_keyword_corpus(Path(path), lambda old: _doc_key(old) in keys, [])
    return {"path": str(path), "kept": kept, "removed": removed}


def read_keyword_documents(path: str | Path, doc_keys: Iterable[Tuple[str, str]]) -> List[Dict[str, Any]]:
    """Rows of these documents (stage 15: promotion copies them)."""
    keys = set(doc_keys)
    if not Path(path).exists():
        return []
    return [row for row in _iter_jsonl(str(path)) if not row.get("__parse_error__") and _doc_key(row) in keys]


def _rewrite_keyword_corpus(target: Path, drop, new_rows: List[Dict[str, Any]]) -> Tuple[int, int]:
    """(kept, dropped). Stage 14: one writer at a time (a sync and an async
    job, several workers), a temp name no other writer uses, and kept rows
    streamed line by line instead of holding the whole corpus in memory."""
    kept = 0
    dropped = 0
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
                                continue
                            if not isinstance(old, dict):
                                continue
                            if drop(old):
                                dropped += 1
                                continue
                            out.write(line if line.endswith("\n") else line + "\n")
                            kept += 1
                for r in new_rows:
                    out.write(json.dumps(r, ensure_ascii=False) + "\n")
            os.replace(tmp, target)
        finally:
            if tmp.exists():
                tmp.unlink()
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
