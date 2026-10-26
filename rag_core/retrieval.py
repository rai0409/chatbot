from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import pickle
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Lock
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple

import config
from rank_bm25 import BM25Okapi
from rag_core import embedder, ja_terms, ja_tokenizer, quantities, store
from rag_core.bm25_index import InvertedBM25
from rag_core.ja_text import (
    active_normalization_version,
    extract_salient_terms_ja,
    normalize_for_index,
    normalize_japanese_text,
)


@dataclass
class RetrievedChunk:
    # Unified comparable score across vector/keyword/hybrid retrieval.
    # Lower is better. This is not guaranteed to be a raw backend score:
    # - vector hit: vector distance
    # - keyword hit: rank-derived pseudo distance (raw BM25 is in metadata["bm25_score"])
    # - hybrid hit: fusion-rank-derived pseudo distance (fused score is in metadata["rrf_score"])
    # This unified score is used by downstream guard/order logic.
    text: str
    metadata: Dict
    score: float


@dataclass
class _KeywordIndex:
    # Stage 14: one copy of each row. The token lists, the searchable and
    # display texts and a second full copy of every row (rows_by_id) used to
    # be kept too, about five times the corpus text per worker.
    texts: List[str]
    norm_docs: List[str]
    metas: List[Dict]  # the row without "text"
    id_to_index: Dict[str, int]
    tenant_ids: List[str]  # normalized, for the keyword lane's tenant filter
    vocabulary: FrozenSet[str]  # tokens of the corpus (typo correction)
    # Exactly one of bm25 (rank_bm25, scans every document per query term)
    # and inverted (postings only) is built, per KEYWORD_INDEX_ENGINE.
    bm25: Optional[BM25Okapi]
    # Tokenizer that built the index; queries must be tokenized the same way.
    tokenizer: str = "heuristic"
    inverted: Optional[InvertedBM25] = None
    # N5 reading lane (READING_LANE_ENABLED): BM25 over katakana readings.
    reading: Optional[InvertedBM25] = None

    def __len__(self) -> int:
        return len(self.texts)

    def display_text(self, idx: int) -> str:
        return str(self.metas[idx].get("display_text") or self.texts[idx] or "")

    def tenant_mask(self, tenant: str):
        """numpy bool array: rows of this (normalized) tenant; built once per tenant."""
        import numpy as np

        masks = self.__dict__.setdefault("_tenant_masks", {})
        mask = masks.get(tenant)
        if mask is None:
            mask = masks[tenant] = np.fromiter((t == tenant for t in self.tenant_ids), dtype=bool,
                                               count=len(self.tenant_ids))
        return mask

    def row(self, chunk_id: str) -> Optional[Dict[str, Any]]:
        """The stored row of one chunk (a new dict), or None."""
        idx = self.id_to_index.get(str(chunk_id or "").strip())
        if idx is None:
            return None
        row = dict(self.metas[idx])
        row["text"] = self.texts[idx]
        return row


_INDEX_CACHE: Dict[str, object] = {"path": None, "mtime": None, "tokenizer": None, "settings": None, "index": None}
# Guards the cache dicts only (short); builds take a per-corpus lock, so a
# staging rebuild never blocks production queries (stage 14).
_INDEX_LOCK = Lock()
_BUILD_LOCKS: Dict[str, Lock] = {}
_REFRESHING: set = set()
_KEYWORD_ENGINES = ("rank_bm25", "inverted")

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
    records = len(index)
    return {
        "keyword_index_loaded": records > 0,
        "keyword_index_records": records,
        "keyword_index_path": str(config.CHUNKS_JSONL_PATH),
    }


def _keyword_settings() -> Tuple[str, bool, float, float]:
    engine = str(getattr(config, "KEYWORD_INDEX_ENGINE", "rank_bm25") or "rank_bm25").strip().lower()
    if engine not in _KEYWORD_ENGINES:
        raise ValueError(f"Unknown KEYWORD_INDEX_ENGINE '{engine}' (supported: {', '.join(_KEYWORD_ENGINES)})")
    return (
        engine,
        bool(getattr(config, "KEYWORD_NUMERIC_TOKENS", False)),
        float(getattr(config, "BM25_K1", 1.5)),
        float(getattr(config, "BM25_B", 0.75)),
    )


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
    # Chroma's where syntax cannot express "missing or equal", so by default
    # the default tenant relies on the authoritative post-query
    # _tenant_matches filter; non-default tenants additionally get a strict
    # equality clause. TENANT_FILTER_STRICT pushes the default tenant into the
    # DB filter too, so other tenants can no longer crowd it out of the
    # oversampled candidates. It requires every record to carry tenant_id
    # (run scripts/backfill_tenant_id.py first).
    tenant = normalize_tenant_id(tenant_id)
    if tenant != DEFAULT_TENANT_ID or bool(getattr(config, "TENANT_FILTER_STRICT", False)):
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
    # Same function as the keyword tokenizer and approved-QA matching (N4).
    return normalize_for_index(text)


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


def _numeric_tokens(text: str) -> List[str]:
    # KEYWORD_NUMERIC_TOKENS: 1万5千円 and 15,000円 both yield
    # q:money:15000JPY, so equal quantities match whatever their notation.
    if not bool(getattr(config, "KEYWORD_NUMERIC_TOKENS", False)):
        return []
    return quantities.quantity_tokens(text)


def _document_tokens(text: str, tokenizer_name: str) -> List[str]:
    # "heuristic" is the legacy character-bigram tokenizer (hiragana-only
    # bigrams dropped); "sudachi" lemmatizes and keeps term frequency.
    if tokenizer_name == "sudachi":
        return ja_tokenizer.sudachi_tokens(text) + _numeric_tokens(text)
    return _heuristic_tokenize(text) + _numeric_tokens(text)


def _query_tokens(question: str, tokenizer_name: str) -> List[str]:
    if tokenizer_name == "sudachi":
        return _unique_preserve(ja_tokenizer.sudachi_tokens(question) + _numeric_tokens(question))
    tokens = _heuristic_tokenize(question)
    if not tokens:
        tokens = _heuristic_tokenize(_normalize(question))
    return _unique_preserve(tokens + _numeric_tokens(question))


_NEIGHBOR_BATCH = 50

# Keyword indexes of non-served collections (staging), keyed by path.
_COLLECTION_INDEX_CACHE: Dict[str, Dict[str, object]] = {}
_COLLECTION_INDEX_CACHE_MAX = 8


def keyword_index_path_for_collection(collection_name: Optional[str]) -> str:
    """Keyword corpus that belongs to a vector collection.

    The served collection uses CHUNKS_JSONL_PATH. Every other collection has
    its own corpus under INDEX_DIR/collections/, so a staging query never
    scores production documents (and the reverse).
    """
    name = str(collection_name or "").strip()
    served = {
        str(getattr(config, "VECTORSTORE_COLLECTION_NAME", "") or "").strip(),
        str(config.getenv_first("CHROMA_COLLECTION") or "").strip(),
        "default",
    }
    if not name or name in served:
        return str(config.CHUNKS_JSONL_PATH)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    if safe != name:
        # Stage 15: a name changed by sanitizing gets a hash suffix, so "a b"
        # and "a_b" (both "a_b") no longer share one corpus file.
        safe = f"{safe}-{hashlib.sha256(name.encode('utf-8')).hexdigest()[:10]}"
    return os.path.join(str(config.INDEX_DIR), "collections", f"{safe}.jsonl")


def _index_for_collection(collection_name: Optional[str]) -> _KeywordIndex:
    # Called without arguments for the served corpus so tests that replace
    # _load_keyword_index with a zero-argument stub keep working.
    path = keyword_index_path_for_collection(collection_name)
    if path == str(config.CHUNKS_JSONL_PATH):
        return _load_keyword_index()
    return _load_keyword_index(path)


def _refresh_mode() -> str:
    """KEYWORD_INDEX_REFRESH: "sync" rebuilds a changed corpus inside the
    request that notices it; "background" keeps serving the previous index
    while a thread rebuilds it (a 50k-document rebuild takes minutes). The
    default is background in production (APP_ENV=production), sync otherwise."""
    raw = str(os.getenv("KEYWORD_INDEX_REFRESH", "")).strip().lower()
    if raw in {"sync", "background"}:
        return raw
    return "background" if str(os.getenv("APP_ENV", "")).strip().lower() == "production" else "sync"


def _build_lock(path: str) -> Lock:
    with _INDEX_LOCK:
        return _BUILD_LOCKS.setdefault(path, Lock())


def _refresh_in_background(path: str, build, store) -> None:
    with _INDEX_LOCK:
        if path in _REFRESHING:
            return
        _REFRESHING.add(path)

    def run() -> None:
        try:
            with _build_lock(path):
                index = build()
            with _INDEX_LOCK:
                store(index)
        except Exception:  # noqa: BLE001 - the previous index keeps serving
            logger.exception("keyword index background rebuild failed: %s", path)
        finally:
            with _INDEX_LOCK:
                _REFRESHING.discard(path)

    threading.Thread(target=run, name="keyword-index-refresh", daemon=True).start()


def _cached_or_build(path: str, read_slot, write_slot) -> _KeywordIndex:
    """Shared by the served and the staging indexes.

    read_slot() -> cached dict or None, write_slot(entry) stores one; both are
    called with _INDEX_LOCK held."""
    mtime = os.path.getmtime(path) if os.path.exists(path) else None
    tokenizer_name = ja_tokenizer.active_tokenizer_name()
    settings = (*_keyword_settings(), active_normalization_version(), _reading_lane_enabled())
    engine, _numeric, k1, b, _normalization, reading = settings

    def entry(index: _KeywordIndex) -> Dict[str, object]:
        return {"path": path, "mtime": mtime, "tokenizer": tokenizer_name, "settings": settings, "index": index}

    def lookup() -> Tuple[Optional[_KeywordIndex], Optional[_KeywordIndex]]:
        cached = read_slot()
        if not cached or cached.get("index") is None or cached.get("path") != path:
            return None, None
        same_build = cached.get("tokenizer") == tokenizer_name and cached.get("settings") == settings
        if same_build and cached.get("mtime") == mtime:
            return cached["index"], None  # type: ignore[return-value]
        return None, (cached["index"] if same_build else None)  # type: ignore[return-value]

    with _INDEX_LOCK:
        fresh, stale = lookup()
    if fresh is not None:
        return fresh

    def build() -> _KeywordIndex:
        return _build_keyword_index(path, tokenizer_name, engine, k1, b, reading)

    if stale is not None and _refresh_mode() == "background":
        _refresh_in_background(path, build, lambda index: write_slot(entry(index)))
        return stale
    with _build_lock(path):
        with _INDEX_LOCK:
            fresh, _ = lookup()  # another request may have built it meanwhile
        if fresh is not None:
            return fresh
        index = build()
        with _INDEX_LOCK:
            write_slot(entry(index))
        return index


def _load_keyword_index(path: Optional[str] = None) -> _KeywordIndex:
    if path is not None and path != str(config.CHUNKS_JSONL_PATH):
        return _load_collection_keyword_index(path)
    return _cached_or_build(str(config.CHUNKS_JSONL_PATH), lambda: _INDEX_CACHE, _INDEX_CACHE.update)


def _reading_lane_enabled() -> bool:
    raw = str(os.getenv("READING_LANE_ENABLED", "")).strip().lower()
    return raw in {"1", "true", "yes", "on"} and ja_terms.morph_available()


def _reading_lane_min_bm25() -> float:
    try:
        return float(str(os.getenv("READING_LANE_MIN_BM25", "")).strip() or 2.0)
    except ValueError:
        return 2.0


# KEYWORD_INDEX_PERSIST=true (stage 9): the built keyword index is pickled
# under INDEX_DIR/.keyword_cache and reused by the next process when the
# corpus file, tokenizer, keyword settings, normalization and the retrieval
# code are unchanged (50k chunks: restart without re-tokenizing).
# Stage 10: every cache file is signed (HMAC-SHA256, key from
# KEYWORD_INDEX_CACHE_KEY or a random key file created next to the cache with
# mode 0600) and only a file with a valid signature is unpickled; several
# workers starting together build the index once (file lock).
_KEYWORD_CACHE_FORMAT = "kwcache-v2"
_KEYWORD_CACHE_MAGIC = b"KWC2"
_KEYWORD_CACHE_KEEP = 8


def _keyword_persist_enabled() -> bool:
    return str(os.getenv("KEYWORD_INDEX_PERSIST", "")).strip().lower() in {"1", "true", "yes", "on"}


def _code_fingerprint() -> List[Any]:
    out: List[Any] = []
    for name in (__name__, ja_tokenizer.__name__, ja_terms.__name__, InvertedBM25.__module__, normalize_for_index.__module__):
        module_file = getattr(sys.modules.get(name), "__file__", None)
        try:
            st = os.stat(module_file) if module_file else None
        except OSError:
            st = None
        out.append([name, st.st_mtime_ns if st else None, st.st_size if st else None])
    return out


def _keyword_cache_file(path: str, tokenizer_name: str, engine: str, k1: float, b: float, reading: bool) -> Optional[str]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = json.dumps(
        [
            _KEYWORD_CACHE_FORMAT, os.path.abspath(path), st.st_mtime_ns, st.st_size, tokenizer_name, engine,
            k1, b, reading, list(_keyword_settings()), active_normalization_version(), _code_fingerprint(),
        ],
        default=str,
    )
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    return os.path.join(str(config.INDEX_DIR), ".keyword_cache", f"{digest}.pkl")


def _prune_keyword_cache(directory: str) -> None:
    try:
        files = sorted(
            (os.path.join(directory, n) for n in os.listdir(directory) if n.endswith(".pkl")),
            key=os.path.getmtime,
            reverse=True,
        )
        for old in files[_KEYWORD_CACHE_KEEP:]:
            os.remove(old)
            if os.path.exists(old + ".lock"):
                os.remove(old + ".lock")
    except OSError:
        pass


def _keyword_cache_key(directory: str) -> bytes:
    configured = str(os.getenv("KEYWORD_INDEX_CACHE_KEY", "")).strip()
    if configured:
        return configured.encode("utf-8")
    os.makedirs(directory, exist_ok=True)
    key_file = os.path.join(directory, ".key")
    try:
        with open(key_file, "rb") as f:
            key = f.read()
        if len(key) >= 32:
            return key
    except FileNotFoundError:
        pass
    key = os.urandom(32)
    fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    return key


def _read_signed_cache(cache_file: str, key: bytes) -> Optional[_KeywordIndex]:
    with open(cache_file, "rb") as f:
        blob = f.read()
    magic, signature, payload = blob[:4], blob[4:36], blob[36:]
    if magic != _KEYWORD_CACHE_MAGIC or not hmac.compare_digest(signature, hmac.new(key, payload, hashlib.sha256).digest()):
        logger.warning("keyword index cache signature mismatch; rebuilding: %s", cache_file)
        return None
    cached = pickle.loads(payload)  # payload is HMAC-verified above  # nosec B301
    return cached if isinstance(cached, _KeywordIndex) else None


def _write_signed_cache(cache_file: str, key: bytes, index: _KeywordIndex) -> None:
    payload = pickle.dumps(index, protocol=pickle.HIGHEST_PROTOCOL)
    tmp = f"{cache_file}.tmp-{os.getpid()}"
    try:
        with open(tmp, "wb") as f:
            f.write(_KEYWORD_CACHE_MAGIC + hmac.new(key, payload, hashlib.sha256).digest() + payload)
        os.replace(tmp, cache_file)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


class _CacheLock:
    """Exclusive file lock (POSIX flock); a no-op where flock is missing."""

    def __init__(self, path: str):
        self.path = path
        self._fd: Optional[int] = None

    def __enter__(self):
        try:
            import fcntl

            self._fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(self._fd, fcntl.LOCK_EX)
        except (ImportError, OSError):
            self._fd = None
        return self

    def __exit__(self, *exc):
        if self._fd is not None:
            import fcntl

            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
        return False


def _build_keyword_index(
    path: str, tokenizer_name: str, engine: str, k1: float, b: float, reading: bool = False
) -> _KeywordIndex:
    if not _keyword_persist_enabled():
        return _build_keyword_index_fresh(path, tokenizer_name, engine, k1, b, reading)
    cache_file = _keyword_cache_file(path, tokenizer_name, engine, k1, b, reading)
    if not cache_file:
        return _build_keyword_index_fresh(path, tokenizer_name, engine, k1, b, reading)
    directory = os.path.dirname(cache_file)
    try:
        key = _keyword_cache_key(directory)
    except OSError:
        logger.warning("keyword index cache key unavailable; building in memory only")
        return _build_keyword_index_fresh(path, tokenizer_name, engine, k1, b, reading)
    with _CacheLock(cache_file + ".lock"):
        if os.path.exists(cache_file):
            try:
                cached = _read_signed_cache(cache_file, key)
                if cached is not None:
                    return cached
            except Exception:  # noqa: BLE001
                logger.warning("keyword index cache unreadable; rebuilding: %s", cache_file)
        index = _build_keyword_index_fresh(path, tokenizer_name, engine, k1, b, reading)
        if index.metas:
            try:
                _write_signed_cache(cache_file, key, index)
                _prune_keyword_cache(directory)
            except Exception:  # noqa: BLE001 - e.g. an engine object that cannot be pickled
                logger.warning("keyword index cache not written: %s", cache_file)
        return index


def _build_keyword_index_fresh(
    path: str, tokenizer_name: str, engine: str, k1: float, b: float, reading: bool = False
) -> _KeywordIndex:
    texts: List[str] = []
    norm_docs: List[str] = []
    metas: List[Dict] = []
    tenant_ids: List[str] = []
    id_to_index: Dict[str, int] = {}
    vocabulary: set = set()
    # Stage 14: the inverted engine (and the reading lane) index each row as it
    # is read; holding every token list until the end made the build peak
    # about six times the finished index, and the process keeps that peak.
    inverted = InvertedBM25(k1=k1, b=b) if engine == "inverted" else None
    reading_index = InvertedBM25(k1=k1, b=b) if reading else None
    tokenized_corpus: List[List[str]] = []  # rank_bm25 only
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
                # Short values repeat on every row (type, tenant, document name):
                # one shared string each instead of one copy per row (stage 14).
                meta = {
                    sys.intern(k): (sys.intern(v) if isinstance(v, str) and len(v) <= 80 else v)
                    for k, v in obj.items() if k != "text"
                }
                searchable_text = _extract_searchable_text(text=text, obj=obj)
                tokens = _document_tokens(searchable_text, tokenizer_name)
                if not tokens:
                    tokens = [str(meta.get("id") or "")]
                texts.append(text)
                norm_docs.append(_normalize(searchable_text))
                metas.append(meta)
                tenant_ids.append(normalize_tenant_id(meta.get("tenant_id")))
                if inverted is not None:
                    inverted.add(tokens)
                else:
                    tokenized_corpus.append(tokens)
                vocabulary.update(tokens)
                if reading_index is not None:
                    reading_index.add(ja_terms.reading_tokens(searchable_text))
                chunk_id = str(meta.get("id") or "").strip()
                if chunk_id:
                    id_to_index[chunk_id] = len(texts) - 1
    if not os.path.exists(path):
        _warn_degraded_keyword_index(path, len(texts), "missing_file")
    elif not texts:
        _warn_degraded_keyword_index(path, len(texts), "empty_corpus")
    if inverted is not None:
        bm25 = None
        inverted.finish()
    else:
        bm25 = BM25Okapi(tokenized_corpus or [[""]], k1=k1, b=b)
    if reading_index is not None:
        reading_index.finish()
    return _KeywordIndex(
        texts=texts,
        norm_docs=norm_docs,
        metas=metas,
        id_to_index=id_to_index,
        tenant_ids=tenant_ids,
        vocabulary=frozenset(vocabulary),
        bm25=bm25,
        tokenizer=tokenizer_name,
        inverted=inverted,
        reading=reading_index,
    )


def chunk_row(chunk_id: str, collection_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The stored row of one chunk (citation excerpts): the served corpus, or
    the keyword corpus of a staging collection."""
    return _index_for_collection(collection_name).row(chunk_id)


def _load_collection_keyword_index(path: str) -> _KeywordIndex:
    def write(entry: Dict[str, object]) -> None:
        if path not in _COLLECTION_INDEX_CACHE and len(_COLLECTION_INDEX_CACHE) >= _COLLECTION_INDEX_CACHE_MAX:
            _COLLECTION_INDEX_CACHE.pop(next(iter(_COLLECTION_INDEX_CACHE)))
        _COLLECTION_INDEX_CACHE[path] = entry

    return _cached_or_build(path, lambda: _COLLECTION_INDEX_CACHE.get(path), write)


def keyword_index_versions(collection_name: Optional[str] = None) -> Dict[str, Dict[str, int]]:
    """Row counts per chunker_version and normalization_version (v2_02 P0-3).

    Rows written before versions were recorded count under "".
    """
    index = _index_for_collection(collection_name)
    out: Dict[str, Dict[str, int]] = {"chunker_version": {}, "normalization_version": {}}
    for meta in index.metas:
        for key in out:
            value = str((meta or {}).get(key) or "")
            out[key][value] = out[key].get(value, 0) + 1
    return out


def keyword_index_generations(collection_name: Optional[str] = None) -> Dict[str, int]:
    """Row count per index_generation in a collection's keyword corpus."""
    index = _index_for_collection(collection_name)
    counts: Dict[str, int] = {}
    for meta in index.metas:
        generation = str((meta or {}).get("index_generation") or "")
        counts[generation] = counts.get(generation, 0) + 1
    return counts


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
    collection_name: Optional[str] = None,
    create_collection_if_missing: bool = True,
) -> List[RetrievedChunk]:
    collection = store.get_vectorstore(
        collection_name=collection_name,
        create_if_missing=create_collection_if_missing,
    )
    where = _build_base_where(allowed_types, allowed_qualities, tenant_id=tenant_id)
    embedding = _resolve_query_embedding(question, client, query_embedding)
    oversample = max(1, int(getattr(config, "CHILD_RETRIEVAL_OVERSAMPLE", 2)))
    n_results = max(top_k, top_k * oversample)
    res = collection.query(
        query_embeddings=[embedding], n_results=n_results, where=_to_chroma_where(where)
    )
    docs = list(res.get("documents", [[]])[0])
    metas = list(res.get("metadatas", [[]])[0])
    dists = list(res.get("distances", [[]])[0])
    if "tenant_id" not in where and len(docs) >= n_results:
        # Stage 14: the default tenant has no DB filter (legacy rows without
        # tenant_id), so other tenants' rows can fill the oversampled window.
        # When fewer than top_k survive the tenant check, ask once more with
        # tenant_id = default and merge (legacy rows stay in the first pass).
        kept = sum(1 for meta in metas if _tenant_matches(dict(meta or {}), tenant_id))
        if kept < top_k:
            strict = dict(where, tenant_id=normalize_tenant_id(tenant_id))
            more = collection.query(query_embeddings=[embedding], n_results=n_results, where=_to_chroma_where(strict))
            seen = {str((meta or {}).get("id") or "") for meta in metas}
            for text, meta, dist in zip(more.get("documents", [[]])[0], more.get("metadatas", [[]])[0],
                                        more.get("distances", [[]])[0]):
                if str((meta or {}).get("id") or "") not in seen:
                    docs.append(text)
                    metas.append(meta)
                    dists.append(dist)
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
    collection_name: Optional[str] = None,
) -> List[RetrievedChunk]:
    index = _index_for_collection(collection_name)
    if not len(index):
        return []
    where = _build_base_where(allowed_types, allowed_qualities, tenant_id=tenant_id)
    where.pop("tenant_id", None)  # tenant is checked against index.tenant_ids below
    query_tokens = _query_tokens(question, index.tokenizer)
    if not query_tokens:
        return []
    tenant = normalize_tenant_id(tenant_id)
    tenant_ids = index.tenant_ids
    # The inverted engine only visits documents sharing a query term, so
    # documents with zero BM25 are no longer padded into the result list.
    # Stage 14: scores and the tenant filter are numpy operations; only the
    # strongest candidates become Python tuples (_bm25_pool).
    if index.inverted is not None:
        matched, scores = index.inverted.score_arrays(query_tokens)
    else:
        import numpy as np

        scores = np.asarray(index.bm25.get_scores(query_tokens), dtype=np.float64)
        matched = np.arange(len(scores))
    exact_terms, quoted_terms, id_terms = _query_match_terms(question)

    def precise(candidates) -> List[Tuple]:
        out_ranked = []
        for idx, bm25_score in candidates:
            meta = index.metas[idx]
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
            out_ranked.append((rank_key, idx, float(bm25_score), quoted_hits, id_hits, exact_hits))
        out_ranked.sort(key=lambda x: x[0], reverse=True)
        return out_ranked

    # Stage 14: the precise ordering (child chunks, quoted / id / exact hits)
    # used to run over every candidate, which with 200,000 chunks and common
    # bigrams is most of the corpus, twice per request. It now runs over the
    # strongest BM25 candidates only, widened while too few survive the filters.
    in_tenant = index.tenant_mask(tenant)
    keep = in_tenant[matched]
    matched, scores = matched[keep], scores[keep]
    limit = max(top_k * 20, 200)
    while True:
        ranked = precise(_bm25_pool(matched, scores, limit))
        if len(ranked) >= top_k or limit >= len(matched):
            break
        limit *= 4
    out = []
    for rank, (_, idx, bm25_score, quoted_hits, id_hits, exact_hits) in enumerate(ranked[:top_k], start=1):
        m = dict(index.metas[idx] or {})
        m["retrieval_source"] = "keyword"
        m["bm25_score"] = bm25_score
        # Exact-match evidence feeds the separate exact lane in hybrid fusion.
        m["exact_quoted_hits"] = quoted_hits
        m["exact_id_hits"] = id_hits
        m["exact_term_hits"] = exact_hits
        # Common lower-is-better scale for downstream guard/order.
        pseudo_distance = min(0.25 + (rank - 1) * 0.02, 0.95)
        out.append(
            RetrievedChunk(
                text=index.display_text(idx),
                metadata=m,
                score=pseudo_distance,
            )
        )
    # Reading lane, only when the surface match is weak (v2_03): too few hits
    # or a low top BM25. It fills the remaining slots, after the surface hits.
    weak = len(ranked) < top_k or (ranked and ranked[0][2] < _reading_lane_min_bm25())
    if index.reading is not None and weak and len(out) < top_k:
        present = {idx for _, idx, *_rest in ranked[:top_k]}
        reading_scores = index.reading.score(ja_terms.reading_tokens(question))
        for idx, score in sorted(reading_scores.items(), key=lambda item: -item[1]):
            if len(out) >= top_k:
                break
            meta = index.metas[idx]
            if idx in present or tenant_ids[idx] != tenant or not _meta_matches_where(meta, where):
                continue
            m = dict(meta or {})
            m["retrieval_source"] = "keyword_reading"
            m["bm25_score"] = float(score)
            out.append(
                RetrievedChunk(
                    text=index.display_text(idx),
                    metadata=m,
                    score=min(0.25 + len(out) * 0.02, 0.95),
                )
            )
    return out


def _bm25_pool(matched, scores, limit: int) -> List[Tuple[int, float]]:
    """The `limit` highest (doc index, BM25) pairs, ties by lower index."""
    import numpy as np

    if len(matched) > limit:
        part = np.argpartition(-scores, limit - 1)[:limit]
        matched, scores = matched[part], scores[part]
    order = np.lexsort((matched, -scores))
    return [(int(matched[i]), float(scores[i])) for i in order]


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


# Hybrid fusion replaces the former hand-tuned score boosts (up to +0.024 on
# top of RRF scores whose rank-1 value is only 1/61 ~= 0.0164 at k=60), which
# let lexical heuristics override the dense signal entirely. Weights and k are
# meant to be tuned on the evaluation set, not by hand.
_FUSION_METHODS = ("weighted_rrf", "dbsf")
_FUSION_LANES = (
    ("dense_rank", "dense_weight"),
    ("keyword_rank", "keyword_weight"),
    ("exact_rank", "exact_weight"),
)


def _fusion_settings() -> Dict[str, Any]:
    method = str(getattr(config, "HYBRID_FUSION", "weighted_rrf") or "weighted_rrf").strip().lower()
    if method not in _FUSION_METHODS:
        raise ValueError(
            f"Unknown HYBRID_FUSION '{method}' (supported: {', '.join(_FUSION_METHODS)})"
        )
    return {
        "method": method,
        "dense_weight": float(getattr(config, "HYBRID_DENSE_WEIGHT", 1.0)),
        "keyword_weight": float(getattr(config, "HYBRID_KEYWORD_WEIGHT", 1.0)),
        "exact_weight": float(getattr(config, "HYBRID_EXACT_WEIGHT", 1.0)),
    }


def _has_exact_hit(chunk: RetrievedChunk) -> bool:
    meta = chunk.metadata or {}
    return int(meta.get("exact_quoted_hits") or 0) > 0 or int(meta.get("exact_id_hits") or 0) > 0


def _dbsf_normalize(scores: Dict[str, float]) -> Dict[str, float]:
    """Distribution-based score fusion normalization.

    Maps one retriever's scores onto [mean - 3σ, mean + 3σ] (not clipped).
    One score, or scores without spread, normalize to 0.5, matching Qdrant's
    documented DBSF behaviour.
    """
    if not scores:
        return {}
    values = list(scores.values())
    if len(values) == 1:
        return {key: 0.5 for key in scores}
    mean = sum(values) / len(values)
    std = (sum((value - mean) ** 2 for value in values) / len(values)) ** 0.5
    if std == 0.0:
        return {key: 0.5 for key in scores}
    low = mean - 3.0 * std
    return {key: (value - low) / (6.0 * std) for key, value in scores.items()}


def _fuse_hybrid_buckets(merged: Dict[str, Dict[str, Any]], *, k_rrf: int, settings: Dict[str, Any]) -> None:
    """Set bucket["fused"] (higher is better) for every merged candidate.

    weighted_rrf: sum over lanes of weight / (k + rank).
    dbsf: per-lane mean±3σ normalized scores (dense similarity = 1 - cosine
    distance, keyword = BM25), weighted and summed; an exact-match hit adds
    the exact weight.
    """
    if settings["method"] == "weighted_rrf":
        for item in merged.values():
            fused = 0.0
            for rank_key, weight_key in _FUSION_LANES:
                rank = item.get(rank_key)
                if rank is not None:
                    fused += float(settings[weight_key]) / (k_rrf + int(rank))
            item["fused"] = fused
        return

    dense: Dict[str, float] = {}
    keyword: Dict[str, float] = {}
    for key, item in merged.items():
        vec = item.get("vector")
        if isinstance(vec, RetrievedChunk):
            distance = (vec.metadata or {}).get("vector_distance", vec.score)
            dense[key] = 1.0 - float(distance)
        kw = item.get("keyword")
        if isinstance(kw, RetrievedChunk):
            keyword[key] = float((kw.metadata or {}).get("bm25_score") or 0.0)
    dense_norm = _dbsf_normalize(dense)
    keyword_norm = _dbsf_normalize(keyword)
    for key, item in merged.items():
        fused = float(settings["dense_weight"]) * dense_norm.get(key, 0.0)
        fused += float(settings["keyword_weight"]) * keyword_norm.get(key, 0.0)
        if item.get("exact_rank") is not None:
            fused += float(settings["exact_weight"])
        item["fused"] = fused


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
    collection_name: Optional[str] = None,
    create_collection_if_missing: bool = True,
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
            collection_name=collection_name,
            create_collection_if_missing=create_collection_if_missing,
        )

    v_top_k = vector_top_k or config.VECTOR_TOP_K or top_k
    k_top_k = bm25_top_k or config.BM25_TOP_K or top_k
    k_rrf = rrf_k or config.HYBRID_RRF_K
    settings = _fusion_settings()

    def _vector() -> List[RetrievedChunk]:
        return vector_retrieve(
            question,
            client,
            top_k=v_top_k,
            allowed_types=allowed_types,
            allowed_qualities=allowed_qualities,
            query_embedding=query_embedding,
            tenant_id=tenant_id,
            collection_name=collection_name,
            create_collection_if_missing=create_collection_if_missing,
        )

    def _keyword() -> List[RetrievedChunk]:
        return keyword_retrieve(
            question,
            top_k=k_top_k,
            allowed_types=allowed_types,
            allowed_qualities=allowed_qualities,
            tenant_id=tenant_id,
            collection_name=collection_name,
        )

    if bool(getattr(config, "HYBRID_PARALLEL", False)):
        # Dense (embedding + ANN) and lexical lanes are independent; running
        # them together makes latency the slower lane instead of the sum.
        with ThreadPoolExecutor(max_workers=2) as pool:
            vector_future = pool.submit(_vector)
            keyword_future = pool.submit(_keyword)
            vector_hits, keyword_hits = vector_future.result(), keyword_future.result()
    else:
        vector_hits, keyword_hits = _vector(), _keyword()

    merged: Dict[str, Dict[str, Any]] = {}

    def _bucket(ch: RetrievedChunk) -> Dict[str, Any]:
        return merged.setdefault(
            _chunk_key(ch),
            {
                "vector": None,
                "keyword": None,
                "dense_rank": None,
                "keyword_rank": None,
                "exact_rank": None,
                "fused": 0.0,
            },
        )

    for rank, ch in enumerate(vector_hits, start=1):
        bucket = _bucket(ch)
        bucket["vector"] = ch
        bucket["dense_rank"] = rank
    for rank, ch in enumerate(keyword_hits, start=1):
        bucket = _bucket(ch)
        bucket["keyword"] = ch
        bucket["keyword_rank"] = rank
    for rank, ch in enumerate((ch for ch in keyword_hits if _has_exact_hit(ch)), start=1):
        _bucket(ch)["exact_rank"] = rank

    _fuse_hybrid_buckets(merged, k_rrf=k_rrf, settings=settings)

    ranked_items = sorted(
        merged.values(),
        key=lambda x: (
            _bucket_child_priority(x),
            float(x["fused"]),
            1 if x.get("keyword") is not None else 0,
        ),
        reverse=True,
    )
    selected_items = ranked_items[:top_k]
    if top_k > 1 and vector_hits and not any(item.get("vector") is not None for item in selected_items):
        best_vector_item = next((item for item in ranked_items if item.get("vector") is not None), None)
        if best_vector_item is not None:
            selected_items = selected_items[: top_k - 1] + [best_vector_item]

    out: List[RetrievedChunk] = []
    for rank, item in enumerate(selected_items, start=1):
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
        fused = float(item["fused"])
        # rrf_score keeps its historical name because qa score_details reads
        # it; it now holds the configured fusion score (weighted RRF default).
        m["rrf_score"] = fused
        m["hybrid_rank_score"] = fused
        m["fusion_method"] = settings["method"]
        for rank_key, _ in _FUSION_LANES:
            if item.get(rank_key) is not None:
                m[f"fusion_{rank_key}"] = int(item[rank_key])
        if k is not None:
            k_meta = k.metadata or {}
            if k_meta.get("bm25_score") is not None:
                m["bm25_score"] = k_meta.get("bm25_score")
            for key in ("exact_quoted_hits", "exact_id_hits", "exact_term_hits"):
                if key in k_meta:
                    m[key] = k_meta[key]
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
    collection_name: Optional[str] = None,
) -> List[RetrievedChunk]:
    if not getattr(config, "ENABLE_PARENT_EXPANSION", True):
        return list(chunks)
    if not chunks:
        return []
    index = _index_for_collection(collection_name)
    if not index.id_to_index:
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

        parent_row = index.row(parent_id)
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
    collection_name: Optional[str] = None,
    create_collection_if_missing: bool = True,
) -> List[RetrievedChunk]:
    """Add the chunks before/after each seed (same doc_id, chunk_index +-window).

    One vector-store request per batch of positions instead of one per seed
    and offset; failures are logged (they used to be swallowed silently).
    """
    out = list(seeds)
    wanted: Dict[Tuple[str, int], float] = {}
    for seed in seeds:
        doc_id = seed.metadata.get("doc_id")
        idx = seed.metadata.get("chunk_index")
        if doc_id is None or idx is None:
            continue
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            continue
        for delta in range(-window, window + 1):
            if delta == 0:
                continue
            key = (str(doc_id), idx + delta)
            wanted[key] = min(wanted.get(key, float("inf")), float(seed.score) + 0.01)
    if not wanted:
        return out
    try:
        collection = store.get_vectorstore(
            collection_name=collection_name,
            create_if_missing=create_collection_if_missing,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("neighbor_chunks_unavailable %s", type(exc).__name__)
        return out
    keys = list(wanted)
    for start in range(0, len(keys), _NEIGHBOR_BATCH):
        batch = keys[start : start + _NEIGHBOR_BATCH]
        clauses = [{"$and": [{"doc_id": doc_id}, {"chunk_index": index}]} for doc_id, index in batch]
        where = clauses[0] if len(clauses) == 1 else {"$or": clauses}
        try:
            res = collection.get(where=where)
        except Exception as exc:  # noqa: BLE001
            logger.warning("neighbor_chunks_lookup_failed %s", type(exc).__name__)
            continue
        for text, meta in zip(res.get("documents", []), res.get("metadatas", [])):
            m = dict(meta or {})
            if not _tenant_matches(m, tenant_id):
                continue
            try:
                key = (str(m.get("doc_id")), int(m.get("chunk_index")))
            except (TypeError, ValueError):
                continue
            if key not in wanted:
                continue
            m["retrieval_source"] = "vector"
            out.append(
                RetrievedChunk(
                    text=_extract_display_text(text=text or "", meta=m),
                    metadata=m,
                    score=wanted[key],
                )
            )
    return out
