from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from threading import Lock
from typing import Any, Dict, List, Optional, Sequence, Tuple

import config
from rank_bm25 import BM25Okapi
from rag_core import embedder, store
from rag_core.ja_text import extract_salient_terms_ja, normalize_japanese_text


@dataclass
class RetrievedChunk:
    # Unified comparable score across vector/keyword/hybrid retrieval.
    # Lower is better. This is not guaranteed to be a raw backend score:
    # - vector hit: vector distance
    # - keyword hit: rank-derived pseudo distance (raw BM25 is in metadata["bm25_score"])
    # - hybrid hit: fusion-rank-derived pseudo distance (RRF is in metadata["rrf_score"])
    # This unified score is used by downstream guard/order logic.
    text: str
    metadata: Dict
    score: float


@dataclass
class _KeywordIndex:
    tokenized_corpus: List[List[str]]
    searchable_docs: List[str]
    display_docs: List[str]
    norm_docs: List[str]
    metas: List[Dict]
    rows_by_id: Dict[str, Dict[str, Any]]
    bm25: BM25Okapi


_INDEX_CACHE: Dict[str, object] = {"path": None, "mtime": None, "index": None}
_INDEX_LOCK = Lock()

logger = logging.getLogger(__name__)

# Degraded keyword corpus states already warned about, keyed by (path, reason),
# so the warning is emitted once per process per state instead of per request.
_KEYWORD_INDEX_WARNED_KEYS: set[Tuple[str, str]] = set()


def _warn_degraded_keyword_index(path: str, records: int, reason: str) -> None:
    key = (str(path), reason)
    if key in _KEYWORD_INDEX_WARNED_KEYS:
        return
    _KEYWORD_INDEX_WARNED_KEYS.add(key)
    logger.warning(
        "keyword_index_degraded %s",
        json.dumps(
            {
                "keyword_index_loaded": records > 0,
                "keyword_index_records": records,
                "keyword_index_path": str(path),
                "reason": reason,
            },
            ensure_ascii=False,
        ),
    )


def keyword_index_status() -> Dict[str, Any]:
    index = _load_keyword_index()
    records = len(index.searchable_docs)
    return {
        "keyword_index_loaded": records > 0,
        "keyword_index_records": records,
        "keyword_index_path": str(config.CHUNKS_JSONL_PATH),
    }


DEFAULT_TENANT_ID = "default"


def normalize_tenant_id(value) -> str:
    text = str(value or "").strip()
    return text or DEFAULT_TENANT_ID


def _tenant_matches(meta: Dict, tenant_id: str) -> bool:
    # Legacy chunks without tenant_id normalize to "default", so they are
    # visible only to the default tenant; non-default tenants require an
    # explicit tag.
    return normalize_tenant_id((meta or {}).get("tenant_id")) == normalize_tenant_id(tenant_id)


def _build_base_where(allowed_types=None, allowed_qualities=None, tenant_id: str = DEFAULT_TENANT_ID) -> Dict:
    where = {}
    if allowed_types:
        where["type"] = {"$in": list(allowed_types)}
    if allowed_qualities:
        where["quality"] = {"$in": list(allowed_qualities)}
    if not config.IGNORE_SEARCHABLE:
        where["searchable"] = 1
    # Chroma's where syntax cannot express "missing or equal", so the default
    # tenant relies on the authoritative post-query _tenant_matches filter;
    # non-default tenants additionally get a strict equality clause.
    tenant = normalize_tenant_id(tenant_id)
    if tenant != DEFAULT_TENANT_ID:
        where["tenant_id"] = tenant
    if config.LOG_WHERE:
        print("where:", json.dumps(where, ensure_ascii=False))
    return where


def _to_chroma_where(where: Optional[Dict]) -> Optional[Dict]:
    # Chroma's where validator accepts exactly one top-level operator, so a flat
    # multi-condition dict (e.g. {"searchable": 1, "tenant_id": "t"}) is invalid
    # and must be expressed as a single $and over one-key clauses. This converter
    # is applied only at the Chroma boundary (collection.query / collection.get);
    # the flat form returned by _build_base_where is preserved for the in-memory
    # _meta_matches_where filter and the keyword path. Conditions are not added
    # or removed, so tenant isolation and searchable filtering are unchanged.
    if not where:
        return None
    items = list(where.items())
    if len(items) == 1:
        key, value = items[0]
        return {key: value}
    return {"$and": [{key: value} for key, value in items]}


def _meta_matches_where(meta: Dict, where: Dict) -> bool:
    if not where:
        return True
    if "type" in where:
        allowed = set(where["type"].get("$in", []))
        if meta.get("type") not in allowed:
            return False
    if "quality" in where:
        allowed = set(where["quality"].get("$in", []))
        if meta.get("quality") not in allowed:
            return False
    if where.get("searchable") == 1:
        if int(meta.get("searchable", 1) or 0) != 1:
            return False
    return True


def _child_role_values() -> set[str]:
    raw = getattr(config, "CHILD_CHUNK_ROLE_VALUES", ["child", "leaf"])
    vals = [str(v).strip().lower() for v in raw if str(v).strip()]
    return set(vals or ["child"])


def _is_parent_chunk(meta: Dict) -> bool:
    role = str((meta or {}).get("chunk_role") or "").strip().lower()
    return role == "parent"


def _is_child_chunk(meta: Dict) -> bool:
    role = str((meta or {}).get("chunk_role") or "").strip().lower()
    if not role:
        # Backward compatibility with older records that had no role metadata.
        return True
    return role in _child_role_values()


def _extract_display_text(*, text: str, meta: Dict) -> str:
    display = str((meta or {}).get("display_text") or "").strip()
    if display:
        return display
    return text or ""


def _extract_searchable_text(*, text: str, obj: Dict) -> str:
    searchable = str((obj or {}).get("searchable_text") or "").strip()
    if searchable:
        return searchable
    return text or ""


def _normalize(text: str) -> str:
    return normalize_japanese_text(text).lower()


def _extract_quoted_terms(text: str) -> List[str]:
    norm = normalize_japanese_text(text or "")
    terms = []
    terms += re.findall(r"「([^」]+)」", norm)
    terms += re.findall(r'"([^"]+)"', norm)
    terms += re.findall(r"'([^']+)'", norm)
    return [_normalize(t) for t in terms if _normalize(t)]


def _unique_preserve(tokens: Sequence[str]) -> List[str]:
    seen = set()
    out = []
    for tok in tokens:
        if not tok:
            continue
        if tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def _heuristic_tokenize(text: str) -> List[str]:
    norm = _normalize(text)
    tokens: List[str] = []
    tokens.extend(_extract_quoted_terms(text))
    tokens.extend(re.findall(r"[a-z0-9][a-z0-9._:/-]{1,}", norm))
    tokens.extend(re.findall(r"[ァ-ヴー]{2,}", norm))
    tokens.extend(re.findall(r"[一-龥々〆〤]{2,}", norm))
    compact = re.sub(r"[^a-z0-9ぁ-んァ-ヴー一-龥々〆〤]", "", norm)
    for i in range(0, max(0, len(compact) - 1)):
        bg = compact[i : i + 2]
        if re.fullmatch(r"[ぁ-ん]{2}", bg):
            continue
        tokens.append(bg)
    tokens = [t for t in tokens if not re.fullmatch(r"[ぁ-ん]{1,3}", t)]
    return _unique_preserve(tokens)


def _query_match_terms(question: str) -> Tuple[List[str], List[str], List[str]]:
    quoted_terms = _extract_quoted_terms(question)
    salient_terms = [_normalize(t) for t in extract_salient_terms_ja(question)]
    alnum_terms = [t for t in salient_terms if re.fullmatch(r"[a-z0-9][a-z0-9._:/-]{1,}", t)]
    id_terms = [t for t in alnum_terms if re.search(r"\d", t)]
    exact_terms = _unique_preserve(quoted_terms + id_terms + alnum_terms + salient_terms)
    return exact_terms, quoted_terms, _unique_preserve(id_terms)


def _load_keyword_index() -> _KeywordIndex:
    path = config.CHUNKS_JSONL_PATH
    mtime = os.path.getmtime(path) if os.path.exists(path) else None
    with _INDEX_LOCK:
        if (
            _INDEX_CACHE["index"] is not None
            and _INDEX_CACHE["path"] == path
            and _INDEX_CACHE["mtime"] == mtime
        ):
            return _INDEX_CACHE["index"]  # type: ignore[return-value]
        searchable_docs: List[str] = []
        display_docs: List[str] = []
        norm_docs: List[str] = []
        metas: List[Dict] = []
        rows_by_id: Dict[str, Dict[str, Any]] = {}
        tokenized_corpus: List[List[str]] = []
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    raw = line.strip()
                    if not raw:
                        continue
                    try:
                        obj = json.loads(raw)
                    except Exception:
                        continue
                    text = str(obj.get("text") or "")
                    if not text:
                        continue
                    meta = {k: v for k, v in obj.items() if k != "text"}
                    searchable_text = _extract_searchable_text(text=text, obj=obj)
                    display_text = str(obj.get("display_text") or text)
                    tokens = _heuristic_tokenize(searchable_text)
                    if not tokens:
                        tokens = [str(meta.get("id") or "")]
                    searchable_docs.append(searchable_text)
                    display_docs.append(display_text)
                    norm_docs.append(_normalize(searchable_text))
                    metas.append(meta)
                    tokenized_corpus.append(tokens)
                    chunk_id = str(meta.get("id") or "").strip()
                    if chunk_id:
                        rows_by_id[chunk_id] = dict(obj)
        if not os.path.exists(path):
            _warn_degraded_keyword_index(path, len(searchable_docs), "missing_file")
        elif not searchable_docs:
            _warn_degraded_keyword_index(path, len(searchable_docs), "empty_corpus")
        bm25 = BM25Okapi(tokenized_corpus or [[""]])
        index = _KeywordIndex(
            tokenized_corpus=tokenized_corpus,
            searchable_docs=searchable_docs,
            display_docs=display_docs,
            norm_docs=norm_docs,
            metas=metas,
            rows_by_id=rows_by_id,
            bm25=bm25,
        )
        _INDEX_CACHE["path"] = path
        _INDEX_CACHE["mtime"] = mtime
        _INDEX_CACHE["index"] = index
        return index


class QueryEmbeddingBatch:
    """Lazily embeds a fixed set of queries in one embed_queries call.

    Embedding happens only when the first vector is requested, so keyword-only
    and stubbed-vector paths never trigger an embedding call at all.
    """

    def __init__(self, queries: Sequence[str], client=None):
        self._queries = list(dict.fromkeys(q for q in queries if q))
        self._client = client
        self._embeddings: Optional[Dict[str, List[float]]] = None

    def get(self, query: str) -> List[float]:
        if self._embeddings is None:
            vectors = embedder.embed_queries(self._queries, client=self._client)
            self._embeddings = {q: list(v) for q, v in zip(self._queries, vectors)}
        if query not in self._embeddings:
            self._embeddings[query] = list(
                embedder.embed_queries([query], client=self._client)[0]
            )
        return self._embeddings[query]


def _resolve_query_embedding(question: str, client, query_embedding) -> List[float]:
    if query_embedding is None:
        return embedder.embed_queries([question], client=client)[0]
    if callable(query_embedding):
        return list(query_embedding())
    return list(query_embedding)


def vector_retrieve(
    question: str,
    client,
    top_k: int,
    allowed_types=None,
    allowed_qualities=None,
    query_embedding=None,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> List[RetrievedChunk]:
    collection = store.get_vectorstore()
    where = _build_base_where(allowed_types, allowed_qualities, tenant_id=tenant_id)
    embedding = _resolve_query_embedding(question, client, query_embedding)
    oversample = max(1, int(getattr(config, "CHILD_RETRIEVAL_OVERSAMPLE", 2)))
    n_results = max(top_k, top_k * oversample)
    res = collection.query(
        query_embeddings=[embedding], n_results=n_results, where=_to_chroma_where(where)
    )
    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    dists = res.get("distances", [[]])[0]
    out: List[RetrievedChunk] = []
    for text, meta, dist in zip(docs, metas, dists):
        m = dict(meta or {})
        if not _tenant_matches(m, tenant_id):
            continue
        m["retrieval_source"] = "vector"
        # Raw cosine distance survives fusion so the guard can use real
        # semantic evidence instead of rank-derived pseudo-distances.
        m["vector_distance"] = float(dist)
        out.append(
            RetrievedChunk(
                text=_extract_display_text(text=text or "", meta=m),
                metadata=m,
                score=float(dist),
            )
        )
    out.sort(key=lambda ch: (0 if _is_child_chunk(ch.metadata) else 1, float(ch.score)))
    return out[:top_k]


def keyword_retrieve(
    question: str,
    top_k: int,
    allowed_types=None,
    allowed_qualities=None,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> List[RetrievedChunk]:
    index = _load_keyword_index()
    if not index.searchable_docs:
        return []
    where = _build_base_where(allowed_types, allowed_qualities, tenant_id=tenant_id)
    where.pop("tenant_id", None)  # tenant is checked via _tenant_matches below
    query_tokens = _heuristic_tokenize(question)
    if not query_tokens:
        query_tokens = _heuristic_tokenize(_normalize(question))
    if not query_tokens:
        return []
    bm25_scores = index.bm25.get_scores(query_tokens)
    exact_terms, quoted_terms, id_terms = _query_match_terms(question)
    ranked = []
    for idx, bm25_score in enumerate(bm25_scores):
        meta = index.metas[idx]
        if not _tenant_matches(meta, tenant_id):
            continue
        if not _meta_matches_where(meta, where):
            continue
        norm_text = index.norm_docs[idx]
        quoted_hits = sum(1 for t in quoted_terms if t and t in norm_text)
        id_hits = sum(1 for t in id_terms if t and t in norm_text)
        exact_hits = sum(1 for t in exact_terms if t and t in norm_text)
        rank_key = (
            1 if _is_child_chunk(meta) else 0,
            1 if quoted_hits > 0 else 0,
            1 if id_hits > 0 else 0,
            exact_hits,
            float(bm25_score),
        )
        ranked.append((rank_key, idx, float(bm25_score)))
    ranked.sort(key=lambda x: x[0], reverse=True)
    out = []
    for rank, (_, idx, bm25_score) in enumerate(ranked[:top_k], start=1):
        m = dict(index.metas[idx] or {})
        m["retrieval_source"] = "keyword"
        m["bm25_score"] = bm25_score
        # Common lower-is-better scale for downstream guard/order.
        pseudo_distance = min(0.25 + (rank - 1) * 0.02, 0.95)
        out.append(
            RetrievedChunk(
                text=index.display_docs[idx] or "",
                metadata=m,
                score=pseudo_distance,
            )
        )
    return out


def _chunk_key(ch: RetrievedChunk) -> str:
    meta = ch.metadata or {}
    return str(meta.get("id") or (ch.text[:120] + str(meta.get("doc_id"))))


def _bucket_base_chunk(bucket: Dict[str, Any]) -> Optional[RetrievedChunk]:
    vec = bucket.get("vector")
    if isinstance(vec, RetrievedChunk):
        return vec
    kw = bucket.get("keyword")
    if isinstance(kw, RetrievedChunk):
        return kw
    return None


def _bucket_child_priority(bucket: Dict[str, Any]) -> int:
    base = _bucket_base_chunk(bucket)
    if base is None:
        return 1
    return 1 if _is_child_chunk(base.metadata) else 0


def hybrid_retrieve(
    question: str,
    client,
    top_k: int,
    allowed_types=None,
    allowed_qualities=None,
    vector_top_k: Optional[int] = None,
    bm25_top_k: Optional[int] = None,
    rrf_k: Optional[int] = None,
    query_embedding=None,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> List[RetrievedChunk]:
    if not config.ENABLE_HYBRID_RETRIEVAL:
        return vector_retrieve(
            question,
            client,
            top_k=top_k,
            allowed_types=allowed_types,
            allowed_qualities=allowed_qualities,
            query_embedding=query_embedding,
            tenant_id=tenant_id,
        )

    v_top_k = vector_top_k or config.VECTOR_TOP_K or top_k
    k_top_k = bm25_top_k or config.BM25_TOP_K or top_k
    k_rrf = rrf_k or config.HYBRID_RRF_K
    vector_hits = vector_retrieve(
        question,
        client,
        top_k=v_top_k,
        allowed_types=allowed_types,
        allowed_qualities=allowed_qualities,
        query_embedding=query_embedding,
        tenant_id=tenant_id,
    )
    keyword_hits = keyword_retrieve(
        question,
        top_k=k_top_k,
        allowed_types=allowed_types,
        allowed_qualities=allowed_qualities,
        tenant_id=tenant_id,
    )

    merged: Dict[str, Dict] = {}
    for rank, ch in enumerate(vector_hits, start=1):
        key = _chunk_key(ch)
        bucket = merged.setdefault(key, {"vector": None, "keyword": None, "rrf": 0.0})
        bucket["vector"] = ch
        bucket["rrf"] += 1.0 / (k_rrf + rank)
    for rank, ch in enumerate(keyword_hits, start=1):
        key = _chunk_key(ch)
        bucket = merged.setdefault(key, {"vector": None, "keyword": None, "rrf": 0.0})
        bucket["keyword"] = ch
        bucket["rrf"] += 1.0 / (k_rrf + rank)

    ranked_items = sorted(
        merged.values(),
        key=lambda x: (
            _bucket_child_priority(x),
            float(x["rrf"]),
        ),
        reverse=True,
    )
    out: List[RetrievedChunk] = []
    for rank, item in enumerate(ranked_items[:top_k], start=1):
        v = item["vector"]
        k = item["keyword"]
        base = v or k
        if base is None:
            continue
        m = dict(base.metadata or {})
        if v is not None and k is not None:
            m["retrieval_source"] = "hybrid"
        elif v is not None:
            m["retrieval_source"] = "vector"
        else:
            m["retrieval_source"] = "keyword"
        m["rrf_score"] = float(item["rrf"])
        # Common lower-is-better scale for downstream guard/order.
        pseudo_distance = min(0.25 + (rank - 1) * 0.02, 0.95)
        if v is not None:
            score = min(float(v.score), pseudo_distance)
        else:
            score = pseudo_distance
        out.append(
            RetrievedChunk(
                text=base.text,
                metadata=m,
                score=score,
            )
        )
    return out


def expand_parent_chunks(
    chunks: Sequence[RetrievedChunk],
    *,
    max_parent_chunks: Optional[int] = None,
    max_parent_context_chars: Optional[int] = None,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> List[RetrievedChunk]:
    if not getattr(config, "ENABLE_PARENT_EXPANSION", True):
        return list(chunks)
    if not chunks:
        return []
    index = _load_keyword_index()
    if not index.rows_by_id:
        return list(chunks)

    parent_limit = max_parent_chunks or int(getattr(config, "MAX_PARENT_EXPANDED_CHUNKS", len(chunks)))
    char_limit = max_parent_context_chars or int(getattr(config, "MAX_PARENT_CONTEXT_CHARS", 2400))

    out: List[RetrievedChunk] = []
    parent_positions: Dict[str, int] = {}
    total_parent_chars = 0

    for ch in chunks:
        meta = dict(ch.metadata or {})
        child_id = str(meta.get("id") or "").strip()
        parent_id = str(meta.get("parent_chunk_id") or "").strip()
        if not parent_id or parent_id == child_id:
            out.append(ch)
            continue

        parent_row = index.rows_by_id.get(parent_id)
        if not parent_row or not _tenant_matches(parent_row, tenant_id):
            out.append(ch)
            continue

        if parent_id in parent_positions:
            pos = parent_positions[parent_id]
            prev = out[pos]
            prev_meta = dict(prev.metadata or {})
            child_ids = [str(x) for x in (prev_meta.get("child_chunk_ids") or []) if str(x).strip()]
            if child_id and child_id not in child_ids:
                child_ids.append(child_id)
            prev_meta["child_chunk_ids"] = child_ids
            if float(ch.score) < float(prev.score):
                prev_meta["primary_child_chunk_id"] = child_id
                prev_meta["source_pages"] = meta.get("source_pages") or prev_meta.get("source_pages") or []
                prev = RetrievedChunk(text=prev.text, metadata=prev_meta, score=float(ch.score))
                out[pos] = prev
            else:
                out[pos] = RetrievedChunk(text=prev.text, metadata=prev_meta, score=float(prev.score))
            continue

        if len(parent_positions) >= parent_limit:
            out.append(ch)
            continue

        parent_meta = {k: v for k, v in parent_row.items() if k != "text"}
        parent_text = str(parent_row.get("display_text") or parent_row.get("text") or "").strip()
        if not parent_text:
            out.append(ch)
            continue

        if char_limit > 0 and total_parent_chars + len(parent_text) > char_limit:
            out.append(ch)
            continue

        merged_meta = dict(parent_meta)
        merged_meta["retrieval_source"] = meta.get("retrieval_source") or merged_meta.get("retrieval_source")
        merged_meta["parent_chunk_id"] = parent_id
        merged_meta["chunk_role"] = "parent"
        merged_meta["expanded_from_parent"] = 1
        merged_meta["retrieved_child_chunk_id"] = child_id
        merged_meta["primary_child_chunk_id"] = child_id or merged_meta.get("primary_child_chunk_id")
        merged_meta["child_chunk_ids"] = [child_id] if child_id else list(merged_meta.get("child_chunk_ids") or [])
        merged_meta["source_doc"] = meta.get("source_doc") or merged_meta.get("source_doc")
        merged_meta["source_pages"] = meta.get("source_pages") or merged_meta.get("source_pages") or []
        merged_meta["parent_source_pages"] = parent_meta.get("source_pages") or []
        merged_meta["searchable_text"] = str(
            parent_row.get("searchable_text") or merged_meta.get("searchable_text") or parent_text
        )
        merged_meta["display_text"] = str(parent_row.get("display_text") or parent_text)

        parent_chunk = RetrievedChunk(
            text=merged_meta["display_text"],
            metadata=merged_meta,
            score=float(ch.score),
        )
        parent_positions[parent_id] = len(out)
        out.append(parent_chunk)
        total_parent_chars += len(parent_text)

    return out


def add_neighbor_chunks(
    seeds: Sequence[RetrievedChunk],
    window: int = 1,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> List[RetrievedChunk]:
    collection = store.get_vectorstore()
    out = list(seeds)
    for seed in seeds:
        doc_id = seed.metadata.get("doc_id")
        idx = seed.metadata.get("chunk_index")
        if doc_id is None or idx is None:
            continue
        for delta in range(-window, window + 1):
            if delta == 0:
                continue
            try:
                res = collection.get(
                    where=_to_chroma_where({"doc_id": doc_id, "chunk_index": idx + delta})
                )
                for text, meta in zip(res.get("documents", []), res.get("metadatas", [])):
                    m = dict(meta or {})
                    if not _tenant_matches(m, tenant_id):
                        continue
                    m["retrieval_source"] = "vector"
                    out.append(
                        RetrievedChunk(
                            text=_extract_display_text(text=text or "", meta=m),
                            metadata=m,
                            score=seed.score + 0.01,
                        )
                    )
            except Exception:
                continue
    return out
