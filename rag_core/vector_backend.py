"""Qdrant behind the Chroma collection interface used by this code base (v2_06).

store.get_vectorstore() returns QdrantCollection when VECTOR_BACKEND=qdrant;
retrieval, ingestion and evaluation keep calling the Chroma-style methods
(query / get / upsert / add / delete / count / modify / metadata / name).

Connection: QDRANT_URL (default http://127.0.0.1:6333) and QDRANT_API_KEY, or
QDRANT_PATH for the embedded local mode (development only: no HNSW, no
quantization). Vectors are cosine; the collection is created on the first
upsert with the vector size of that batch.
Tuning (measured with eval.bench): QDRANT_HNSW_M, QDRANT_HNSW_EF_CONSTRUCT,
QDRANT_SEARCH_EF, QDRANT_QUANTIZATION=int8 (scalar, rescored).

Ids: Qdrant needs UUIDs, so each string id becomes uuid5(id); the original id
is kept in the payload (_id). Chroma-style where clauses ($and, $or, equality,
$eq, $ne, $in, $nin, $gt/$gte/$lt/$lte) are translated to Qdrant filters.
Collection metadata (embedding fingerprint) is kept in Qdrant itself, in the
payload of one point of QDRANT_META_COLLECTION (default "_kuraden_meta"), so
every API node sees the same value; the sidecar JSON file under
VECTORSTORE_DIR/qdrant_meta/ is still written and read as a fallback.

Connection and resilience (stage 10): QDRANT_PREFER_GRPC=true uses gRPC
(port QDRANT_GRPC_PORT, default 6334), QDRANT_CA_CERT verifies a private CA
over https, transient errors are retried (QDRANT_RETRIES, default 2, with
backoff), upserts and deletes go in batches (QDRANT_UPSERT_BATCH, default
256), several query vectors use one batch request, collection existence is
cached for QDRANT_EXISTS_CACHE_SECONDS (default 60), and tenant_id is a
tenant-optimised payload index (Qdrant >= 1.11). health() is on /admin/health.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional, Sequence

import config

_ID_NAMESPACE = uuid.UUID("6f1c3c58-6a8e-4c43-9a3e-2d6f0f9b1d10")
_ID_KEY = "_id"
_DOC_KEY = "_document"
_INDEXED_KEYWORD_FIELDS = ("tenant_id", "source_doc", "doc_id", "doc_type", "chunk_type", "chunk_role", "type", "quality", "index_generation")
_INDEXED_INTEGER_FIELDS = ("searchable", "chunk_index")

_CLIENT_LOCK = Lock()
_CLIENT: Dict[str, Any] = {}
# Keyed by (client, collection): a new client (other server or path) starts fresh.
_EXISTS: Dict[tuple, float] = {}
_META_CACHE: Dict[tuple, tuple] = {}
_TRANSIENT_NAMES = {
    "ResponseHandlingException", "ConnectError", "ConnectTimeout", "ReadTimeout", "WriteTimeout",
    "PoolTimeout", "RemoteProtocolError", "TimeoutException", "_InactiveRpcError", "RpcError",
}

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, "")).strip() or default)
    except ValueError:
        return default


def _transient(exc: BaseException) -> bool:
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    return type(exc).__name__ in _TRANSIENT_NAMES


def with_retry(call, *args, **kwargs):
    """Run a Qdrant call, retrying connection/timeout errors with backoff."""
    retries = _env_int("QDRANT_RETRIES", 2)
    for attempt in range(retries + 1):
        try:
            return call(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            if attempt >= retries or not _transient(exc):
                raise
            time.sleep(min(2.0, 0.2 * (2 ** attempt)))
    raise RuntimeError("unreachable")


def backend_name() -> str:
    return str(os.getenv("VECTOR_BACKEND", "chroma")).strip().lower() or "chroma"


def _client():
    from qdrant_client import QdrantClient

    path = str(os.getenv("QDRANT_PATH", "")).strip()
    url = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
    key = f"path:{path}" if path else f"url:{url}"
    with _CLIENT_LOCK:
        client = _CLIENT.get(key)
        if client is None:
            if path:
                client = QdrantClient(path=path)
            else:
                kwargs: Dict[str, Any] = {
                    "url": url,
                    "api_key": os.getenv("QDRANT_API_KEY") or None,
                    "timeout": float(os.getenv("QDRANT_TIMEOUT_SECONDS", "30")),
                }
                if str(os.getenv("QDRANT_PREFER_GRPC", "")).strip().lower() in {"1", "true", "yes", "on"}:
                    kwargs["prefer_grpc"] = True
                    kwargs["grpc_port"] = _env_int("QDRANT_GRPC_PORT", 6334)
                ca = str(os.getenv("QDRANT_CA_CERT", "")).strip()
                try:
                    # Extra keyword arguments go to the HTTP client (verify = CA bundle).
                    client = QdrantClient(**kwargs, **({"verify": ca} if ca else {}))
                except TypeError:
                    client = QdrantClient(**kwargs)
            _CLIENT[key] = client
        return client


def health() -> Dict[str, Any]:
    if backend_name() != "qdrant":
        return {}
    started = time.monotonic()
    try:
        with_retry(_client().get_collections)
        return {"qdrant": {"ok": True, "ms": round((time.monotonic() - started) * 1000, 1)}}
    except Exception as exc:  # noqa: BLE001
        return {"qdrant": {"ok": False, "error": type(exc).__name__}}


def point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(_ID_NAMESPACE, str(chunk_id)))


# --- where translation -----------------------------------------------------------------


def translate_where(where: Optional[Dict[str, Any]]):
    """Chroma where dict -> qdrant_client.models.Filter (None for no filter)."""
    from qdrant_client import models

    def _field(key: str, cond: Any) -> List[Any]:
        if not isinstance(cond, dict):
            cond = {"$eq": cond}
        must: List[Any] = []
        must_not: List[Any] = []
        for op, value in cond.items():
            if op == "$eq":
                must.append(models.FieldCondition(key=key, match=models.MatchValue(value=value)))
            elif op == "$ne":
                must_not.append(models.FieldCondition(key=key, match=models.MatchValue(value=value)))
            elif op == "$in":
                must.append(models.FieldCondition(key=key, match=models.MatchAny(any=list(value))))
            elif op == "$nin":
                must_not.append(models.FieldCondition(key=key, match=models.MatchAny(any=list(value))))
            elif op in {"$gt", "$gte", "$lt", "$lte"}:
                must.append(models.FieldCondition(key=key, range=models.Range(**{op[1:]: value})))
            else:
                raise ValueError(f"unsupported where operator {op}")
        if must_not:
            return [models.Filter(must=must or None, must_not=must_not)]
        return must

    def _node(node: Dict[str, Any]):
        if "$and" in node:
            return models.Filter(must=[_node(n) for n in node["$and"]])
        if "$or" in node:
            return models.Filter(should=[_node(n) for n in node["$or"]])
        conditions: List[Any] = []
        for key, cond in node.items():
            conditions.extend(_field(key, cond))
        return models.Filter(must=conditions)

    if not where:
        return None
    return _node(where)


# --- collection --------------------------------------------------------------------------


class QdrantCollection:
    def __init__(self, name: str, *, client=None):
        self.name = name
        self._client = client or _client()

    @property
    def _cache_key(self) -> tuple:
        return (id(self._client), self.name)

    # metadata (shared in Qdrant, sidecar file as fallback) ------------------------------
    def _meta_path(self) -> Path:
        return Path(config.VECTORSTORE_DIR) / "qdrant_meta" / f"{self.name}.json"

    @staticmethod
    def _meta_collection() -> str:
        return str(os.getenv("QDRANT_META_COLLECTION", "_kuraden_meta")).strip() or "_kuraden_meta"

    def _remote_meta(self) -> Optional[Dict[str, Any]]:
        cached = _META_CACHE.get(self._cache_key)
        if cached and time.monotonic() - cached[0] < 30:
            return dict(cached[1])
        try:
            meta_name = self._meta_collection()
            if not self._client.collection_exists(meta_name):
                return None
            records = with_retry(self._client.retrieve, meta_name, ids=[point_id(self.name)], with_payload=True)
        except Exception:  # noqa: BLE001 - fall back to the sidecar file
            return None
        if not records:
            return None
        meta = json.loads(str((records[0].payload or {}).get("metadata") or "{}"))
        _META_CACHE[self._cache_key] = (time.monotonic(), meta)
        return dict(meta)

    def _write_remote_meta(self, meta: Dict[str, Any]) -> None:
        from qdrant_client import models

        meta_name = self._meta_collection()
        if not self._client.collection_exists(meta_name):
            self._client.create_collection(
                collection_name=meta_name, vectors_config=models.VectorParams(size=1, distance=models.Distance.DOT)
            )
        point = models.PointStruct(
            id=point_id(self.name),
            vector=[1.0],
            payload={"collection": self.name, "metadata": json.dumps(meta, ensure_ascii=False)},
        )
        with_retry(self._client.upsert, meta_name, points=[point], wait=True)
        _META_CACHE[self._cache_key] = (time.monotonic(), dict(meta))

    def _sidecar_meta(self) -> Dict[str, Any]:
        try:
            return json.loads(self._meta_path().read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {}

    @property
    def metadata(self) -> Dict[str, Any]:
        remote = self._remote_meta()
        return remote if remote is not None else self._sidecar_meta()

    def modify(self, metadata: Optional[Dict[str, Any]] = None, **_ignored) -> None:
        merged = {**self.metadata, **dict(metadata or {})}
        try:
            self._write_remote_meta(merged)
        except Exception:  # noqa: BLE001
            logger.warning("qdrant collection metadata kept only in the sidecar file (%s)", self.name)
        path = self._meta_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
        tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    # lifecycle ----------------------------------------------------------------------------
    def exists(self) -> bool:
        # Positive answers are cached: query() used to ask the server on every call.
        seen = _EXISTS.get(self._cache_key)
        if seen is not None and time.monotonic() - seen < _env_int("QDRANT_EXISTS_CACHE_SECONDS", 60):
            return True
        found = bool(with_retry(self._client.collection_exists, self.name))
        if found:
            _EXISTS[self._cache_key] = time.monotonic()
        else:
            _EXISTS.pop(self._cache_key, None)
        return found

    def _ensure(self, dim: int) -> None:
        from qdrant_client import models

        if self.exists():
            return
        quantization = None
        if str(os.getenv("QDRANT_QUANTIZATION", "")).strip().lower() == "int8":
            quantization = models.ScalarQuantization(
                scalar=models.ScalarQuantizationConfig(type=models.ScalarType.INT8, always_ram=True)
            )
        self._client.create_collection(
            collection_name=self.name,
            vectors_config=models.VectorParams(size=int(dim), distance=models.Distance.COSINE),
            hnsw_config=models.HnswConfigDiff(
                m=int(os.getenv("QDRANT_HNSW_M", "16")),
                ef_construct=int(os.getenv("QDRANT_HNSW_EF_CONSTRUCT", "128")),
            ),
            quantization_config=quantization,
        )
        _EXISTS[self._cache_key] = time.monotonic()
        for field in _INDEXED_KEYWORD_FIELDS:
            schema: Any = models.PayloadSchemaType.KEYWORD
            if field == "tenant_id":
                try:
                    # Tenant-optimised index: points of one tenant are stored
                    # together and tenant-filtered search is faster (>= 1.11).
                    schema = models.KeywordIndexParams(type=models.KeywordIndexType.KEYWORD, is_tenant=True)
                except (AttributeError, TypeError):
                    schema = models.PayloadSchemaType.KEYWORD
            try:
                self._client.create_payload_index(self.name, field_name=field, field_schema=schema)
            except Exception:  # noqa: BLE001 - older server without is_tenant
                self._client.create_payload_index(self.name, field_name=field, field_schema=models.PayloadSchemaType.KEYWORD)
        for field in _INDEXED_INTEGER_FIELDS:
            self._client.create_payload_index(self.name, field_name=field, field_schema=models.PayloadSchemaType.INTEGER)

    def count(self) -> int:
        if not self.exists():
            return 0
        return int(with_retry(self._client.count, self.name, exact=True).count)

    # writes --------------------------------------------------------------------------------
    def upsert(self, ids: Sequence[str], documents: Sequence[str], embeddings: Sequence[Sequence[float]], metadatas: Sequence[Dict[str, Any]]) -> None:
        from qdrant_client import models

        if not ids:
            return
        self._ensure(len(embeddings[0]))
        points = [
            models.PointStruct(
                id=point_id(chunk_id),
                vector=list(map(float, vector)),
                payload={**dict(meta or {}), _ID_KEY: str(chunk_id), _DOC_KEY: str(doc or "")},
            )
            for chunk_id, doc, vector, meta in zip(ids, documents, embeddings, metadatas)
        ]
        batch = max(1, _env_int("QDRANT_UPSERT_BATCH", 256))
        for start in range(0, len(points), batch):
            with_retry(self._client.upsert, self.name, points=points[start:start + batch], wait=True)

    add = upsert

    def delete(self, ids: Optional[Sequence[str]] = None, where: Optional[Dict[str, Any]] = None) -> None:
        from qdrant_client import models

        if not self.exists():
            return
        if ids:
            batch = max(1, _env_int("QDRANT_UPSERT_BATCH", 256))
            point_ids = [point_id(i) for i in ids]
            for start in range(0, len(point_ids), batch):
                selector = models.PointIdsList(points=point_ids[start:start + batch])
                with_retry(self._client.delete, self.name, points_selector=selector, wait=True)
            return
        if not where:
            return
        selector = models.FilterSelector(filter=translate_where(where))
        with_retry(self._client.delete, self.name, points_selector=selector, wait=True)

    # reads -----------------------------------------------------------------------------------
    @staticmethod
    def _split(payload: Dict[str, Any]) -> tuple[str, str, Dict[str, Any]]:
        meta = dict(payload or {})
        chunk_id = str(meta.pop(_ID_KEY, ""))
        document = str(meta.pop(_DOC_KEY, ""))
        return chunk_id, document, meta

    def query(self, query_embeddings: Sequence[Sequence[float]], n_results: int = 10, where=None, include=None, **_ignored) -> Dict[str, Any]:
        from qdrant_client import models

        out: Dict[str, List[List[Any]]] = {"ids": [], "documents": [], "metadatas": [], "distances": []}
        if not self.exists():
            for key in out:
                out[key] = [[] for _ in query_embeddings]
            return out
        quantized = str(os.getenv("QDRANT_QUANTIZATION", "")).strip().lower() == "int8"
        params = models.SearchParams(
            hnsw_ef=int(os.getenv("QDRANT_SEARCH_EF", "128")),
            # int8 search is rescored with the full vectors (oversampled 2x).
            quantization=models.QuantizationSearchParams(rescore=True, oversampling=2.0) if quantized else None,
        )
        query_filter = translate_where(where)
        vectors = [list(map(float, v)) for v in query_embeddings]
        responses = None
        if len(vectors) > 1 and hasattr(self._client, "query_batch_points"):
            # One round trip for several query vectors.
            requests = [
                models.QueryRequest(query=v, limit=int(n_results), filter=query_filter, params=params, with_payload=True)
                for v in vectors
            ]
            responses = with_retry(self._client.query_batch_points, self.name, requests=requests)
        if responses is None:
            responses = [
                with_retry(
                    self._client.query_points,
                    self.name,
                    query=v,
                    limit=int(n_results),
                    query_filter=query_filter,
                    search_params=params,
                    with_payload=True,
                )
                for v in vectors
            ]
        for response in responses:
            ids, docs, metas, dists = [], [], [], []
            for point in response.points:
                chunk_id, document, meta = self._split(point.payload)
                ids.append(chunk_id)
                docs.append(document)
                metas.append(meta)
                dists.append(1.0 - float(point.score))  # cosine distance, as Chroma reports
            out["ids"].append(ids)
            out["documents"].append(docs)
            out["metadatas"].append(metas)
            out["distances"].append(dists)
        return out

    def get(self, ids: Optional[Sequence[str]] = None, where=None, include=None, limit: Optional[int] = None, **_ignored) -> Dict[str, Any]:
        out: Dict[str, List[Any]] = {"ids": [], "documents": [], "metadatas": []}
        # include=["embeddings"] returns vectors too (evaluation and migration
        # read the embedding size this way).
        with_vectors = bool(include and "embeddings" in include)
        if with_vectors:
            out["embeddings"] = []
        if not self.exists():
            return out
        page = min(256, int(limit)) if limit else 256
        if ids:
            records = with_retry(
                self._client.retrieve, self.name, ids=[point_id(i) for i in ids], with_payload=True, with_vectors=with_vectors
            )
        else:
            records, offset = [], None
            while True:
                batch, offset = with_retry(
                    self._client.scroll,
                    self.name,
                    scroll_filter=translate_where(where),
                    limit=page,
                    offset=offset,
                    with_payload=True,
                    with_vectors=with_vectors,
                )
                records.extend(batch)
                if offset is None or (limit is not None and len(records) >= limit):
                    break
        for record in records[: limit or None]:
            chunk_id, document, meta = self._split(record.payload)
            out["ids"].append(chunk_id)
            out["documents"].append(document)
            out["metadatas"].append(meta)
            if with_vectors:
                out["embeddings"].append(list(record.vector or []))
        return out


def get_collection(name: str, *, create_if_missing: bool = True) -> QdrantCollection:
    collection = QdrantCollection(name)
    if not create_if_missing and not collection.exists():
        raise ValueError(f"qdrant collection {name} does not exist")
    return collection
