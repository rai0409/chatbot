"""Vector DB backends for side-by-side comparison under the same vectors.

exact   DenseIndex (numpy); also the reference for ANN recall
chroma  chromadb in-process (HNSW). Chroma 1.x takes HNSW settings through
        `configuration`; older versions through "hnsw:*" metadata. Both are
        tried and the path used is reported.
qdrant  qdrant-client against a server (url) or ":memory:". The local
        ":memory:" mode is for functional checks only: it does not run the
        server's HNSW/quantization, so latency/ANN-recall numbers must come
        from a real server (e.g. the official container).
Every backend filters by tenant_id and reports build time and an estimated
memory footprint (vectors + HNSW links: n * m * 2 * 4 bytes, an estimate).
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from eval.bench.encoders import DenseIndex

_BATCH = 1000


def _hnsw_bytes(n: int, m: int) -> int:
    return int(n * m * 2 * 4)


class ExactBackend:
    name = "exact"

    def __init__(self, params: Dict[str, Any]):
        self.params = params
        self.index: Optional[DenseIndex] = None
        self.build_seconds = 0.0

    def build(self, vectors: np.ndarray, tenants: Sequence[str]) -> None:
        started = time.perf_counter()
        self.index = DenseIndex(
            vectors,
            tenants,
            quantization=self.params.get("quantization", "float32"),
            rescore_oversample=int(self.params.get("rescore_oversample", 1)),
        )
        self.build_seconds = time.perf_counter() - started

    def search(self, query: np.ndarray, k: int, tenant_id: Optional[str]) -> List[Tuple[int, float]]:
        return self.index.search(query, k, tenant_id)

    def describe(self) -> Dict[str, Any]:
        out = self.index.describe()
        out.update({"build_seconds": self.build_seconds, "memory_bytes_estimate": out["memory_bytes"]})
        return out

    def close(self) -> None:
        self.index = None


class ChromaBackend:
    name = "chroma"

    def __init__(self, params: Dict[str, Any]):
        self.params = params
        self.m = int(params.get("m", 16))
        self.ef_construction = int(params.get("ef_construction", 200))
        self.ef_search = int(params.get("ef_search", 64))
        self.client = None
        self.collection = None
        self.config_path = ""
        self.build_seconds = 0.0
        self.count = 0
        self.dimensions = 0

    def build(self, vectors: np.ndarray, tenants: Sequence[str]) -> None:
        import chromadb

        self.client = chromadb.EphemeralClient()
        name = f"bench_{uuid.uuid4().hex[:12]}"
        hnsw = {"space": "cosine", "max_neighbors": self.m, "ef_construction": self.ef_construction, "ef_search": self.ef_search}
        try:
            self.collection = self.client.create_collection(name=name, configuration={"hnsw": hnsw})
            self.config_path = "configuration"
        except Exception:
            self.collection = self.client.create_collection(
                name=name,
                metadata={
                    "hnsw:space": "cosine",
                    "hnsw:M": self.m,
                    "hnsw:construction_ef": self.ef_construction,
                    "hnsw:search_ef": self.ef_search,
                },
            )
            self.config_path = "metadata"
        started = time.perf_counter()
        for start in range(0, len(vectors), _BATCH):
            end = min(start + _BATCH, len(vectors))
            self.collection.add(
                ids=[str(i) for i in range(start, end)],
                embeddings=vectors[start:end].tolist(),
                metadatas=[{"tenant_id": str(tenants[i])} for i in range(start, end)],
            )
        self.build_seconds = time.perf_counter() - started
        self.count, self.dimensions = int(vectors.shape[0]), int(vectors.shape[1])

    def search(self, query: np.ndarray, k: int, tenant_id: Optional[str]) -> List[Tuple[int, float]]:
        res = self.collection.query(
            query_embeddings=[query.tolist()],
            n_results=k,
            where={"tenant_id": tenant_id} if tenant_id is not None else None,
        )
        ids = (res.get("ids") or [[]])[0]
        distances = (res.get("distances") or [[]])[0]
        return [(int(i), 1.0 - float(d)) for i, d in zip(ids, distances)]

    def describe(self) -> Dict[str, Any]:
        return {
            "type": "chroma",
            "m": self.m,
            "ef_construction": self.ef_construction,
            "ef_search": self.ef_search,
            "config_path": self.config_path,
            "build_seconds": self.build_seconds,
            "memory_bytes_estimate": self.count * self.dimensions * 4 + _hnsw_bytes(self.count, self.m),
        }

    def close(self) -> None:
        if self.client is not None and self.collection is not None:
            try:
                self.client.delete_collection(self.collection.name)
            except Exception:
                pass
        self.client = self.collection = None


class QdrantBackend:
    name = "qdrant"

    def __init__(self, params: Dict[str, Any]):
        self.params = params
        self.url = params.get("url")
        self.m = int(params.get("m", 16))
        self.ef_construction = int(params.get("ef_construction", 200))
        self.ef_search = int(params.get("ef_search", 64))
        self.quantization = params.get("quantization", "float32")
        self.oversampling = float(params.get("rescore_oversample", 2.0))
        self.client = None
        self.collection = ""
        self.models = None
        self.build_seconds = 0.0
        self.count = 0
        self.dimensions = 0

    def _quantization_config(self):
        models = self.models
        if self.quantization == "int8":
            return models.ScalarQuantization(
                scalar=models.ScalarQuantizationConfig(type=models.ScalarType.INT8, quantile=0.99, always_ram=True)
            )
        if self.quantization == "binary":
            return models.BinaryQuantization(binary=models.BinaryQuantizationConfig(always_ram=True))
        return None

    def build(self, vectors: np.ndarray, tenants: Sequence[str]) -> None:
        from qdrant_client import QdrantClient, models

        self.models = models
        self.client = QdrantClient(url=self.url) if self.url else QdrantClient(location=":memory:")
        self.collection = f"bench_{uuid.uuid4().hex[:12]}"
        self.client.create_collection(
            collection_name=self.collection,
            vectors_config=models.VectorParams(size=int(vectors.shape[1]), distance=models.Distance.COSINE),
            hnsw_config=models.HnswConfigDiff(m=self.m, ef_construct=self.ef_construction),
            quantization_config=self._quantization_config(),
        )
        self.client.create_payload_index(
            collection_name=self.collection, field_name="tenant_id", field_schema=models.PayloadSchemaType.KEYWORD
        )
        started = time.perf_counter()
        for start in range(0, len(vectors), _BATCH):
            end = min(start + _BATCH, len(vectors))
            self.client.upsert(
                collection_name=self.collection,
                points=[
                    models.PointStruct(id=i, vector=vectors[i].tolist(), payload={"tenant_id": str(tenants[i])})
                    for i in range(start, end)
                ],
                wait=True,
            )
        self.build_seconds = time.perf_counter() - started
        self.count, self.dimensions = int(vectors.shape[0]), int(vectors.shape[1])

    def search(self, query: np.ndarray, k: int, tenant_id: Optional[str]) -> List[Tuple[int, float]]:
        models = self.models
        quant_params = None
        if self.quantization != "float32":
            quant_params = models.QuantizationSearchParams(rescore=True, oversampling=self.oversampling)
        res = self.client.query_points(
            collection_name=self.collection,
            query=query.tolist(),
            limit=k,
            query_filter=(
                models.Filter(must=[models.FieldCondition(key="tenant_id", match=models.MatchValue(value=tenant_id))])
                if tenant_id is not None
                else None
            ),
            search_params=models.SearchParams(hnsw_ef=self.ef_search, quantization=quant_params),
            with_payload=False,
        )
        return [(int(point.id), float(point.score)) for point in res.points]

    def describe(self) -> Dict[str, Any]:
        bytes_per_dim = {"float32": 4.0, "int8": 1.0, "binary": 1 / 8}[self.quantization]
        return {
            "type": "qdrant",
            "server": bool(self.url),
            "m": self.m,
            "ef_construction": self.ef_construction,
            "ef_search": self.ef_search,
            "quantization": self.quantization,
            "rescore_oversample": self.oversampling,
            "build_seconds": self.build_seconds,
            # Quantized codes in RAM; original float32 vectors kept for rescoring.
            "memory_bytes_estimate": int(self.count * self.dimensions * bytes_per_dim) + _hnsw_bytes(self.count, self.m),
        }

    def close(self) -> None:
        if self.client is not None and self.collection:
            try:
                self.client.delete_collection(self.collection)
            except Exception:
                pass
        self.client = None


_BACKENDS = {"exact": ExactBackend, "chroma": ChromaBackend, "qdrant": QdrantBackend}


def make_backend(spec: Dict[str, Any], quantization: Dict[str, Any]):
    kind = spec.get("type", "exact")
    if kind not in _BACKENDS:
        raise ValueError(f"unknown backend '{kind}' (supported: {', '.join(_BACKENDS)})")
    params = dict(spec)
    params["quantization"] = quantization.get("type", "float32")
    params["rescore_oversample"] = quantization.get("rescore_oversample", params.get("rescore_oversample", 1))
    if kind == "chroma" and params["quantization"] != "float32":
        raise ValueError("chroma backend has no vector quantization; use exact or qdrant for quantization sweeps")
    return _BACKENDS[kind](params)
