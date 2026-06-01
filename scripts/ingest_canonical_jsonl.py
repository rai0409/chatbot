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
import sys
from typing import Any, Dict, Iterable, List, Optional, Tuple

import config
from rag_core import embedder, store
from rag_core.utils import ensure_openai_client


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
            return store.get_vectorstore(collection_name=name)
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("canonical_jsonl_path")
    parser.add_argument("--batch", type=int, default=64)
    parser.add_argument("--collection", default=None)
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    client = None
    if not embedder.is_local_provider():
        client = ensure_openai_client(base_url=config.OPENAI_BASE_URL)

    collection = store.get_vectorstore(collection_name=args.collection)
    if args.reset:
        collection = _reset_collection(collection)

    batch_ids: List[str] = []
    batch_texts: List[str] = []
    batch_metas: List[Dict[str, Any]] = []
    ingested = 0
    skipped = 0

    def flush_batch():
        nonlocal ingested
        if not batch_ids:
            return
        embeddings = embedder.embed_queries(batch_texts, client=client)
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
        batch_metas.clear()
        print(f"ingested={ingested} skipped={skipped}")

    for row in _iter_jsonl(args.canonical_jsonl_path):
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
        meta = dict(row)
        meta["id"] = str(raw_id)
        meta.pop("text", None)
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

        batch_ids.append(str(raw_id))
        batch_texts.append(str(text))
        batch_metas.append(meta)

        if len(batch_ids) >= args.batch:
            try:
                flush_batch()
            except Exception as exc:
                print(f"error: failed to upsert batch: {exc}", file=sys.stderr)
                return 1

    if batch_ids:
        try:
            flush_batch()
        except Exception as exc:
            print(f"error: failed to upsert batch: {exc}", file=sys.stderr)
            return 1

    print(f"done ingested={ingested} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
