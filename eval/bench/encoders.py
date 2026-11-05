"""Embedding experiments: cached encoding, dimension reduction, quantization.

- encode(): sentence-transformers with the model's retrieval prefixes; the
  matrix is cached under runs/bench_cache keyed by model, prefixes, max
  sequence length and the exact texts, so sweeps never re-encode.
- Projection: "none", "truncate:N" (only meaningful for Matryoshka-trained
  models) or "pca:N" (fitted on the corpus vectors); rows are re-normalized.
- DenseIndex: exact search over float32 / int8 / binary codes with optional
  float32 rescoring of an oversampled candidate set. It isolates the effect
  of dimensions and quantization from any ANN index.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from rag_core import embedding_provider
from eval.bench.stats import latency_summary, percentile


@dataclass(frozen=True)
class EmbeddingSpec:
    model: str
    revision: Optional[str] = None
    local_model_path: Optional[str] = None
    prefix: str = "auto"  # auto | none | custom
    query_prefix: str = ""
    document_prefix: str = ""
    max_seq_length: Optional[int] = None
    batch_size: int = 32

    def prefixes(self) -> Tuple[str, str]:
        if self.prefix == "auto":
            return embedding_provider._model_default_prefixes(self.model)
        if self.prefix == "none":
            return "", ""
        if self.prefix == "custom":
            return self.query_prefix, self.document_prefix
        raise ValueError(f"unknown prefix mode '{self.prefix}' (supported: auto, none, custom)")

    def key(self) -> str:
        payload = dict(asdict(self), resolved_prefixes=self.prefixes())
        payload.pop("batch_size")
        return hashlib.sha1(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


_MODELS: Dict[Tuple[str, Optional[str], Optional[str], Optional[int]], Any] = {}


def load_model(spec: EmbeddingSpec):
    key = (spec.model, spec.revision, spec.local_model_path, spec.max_seq_length)
    if key not in _MODELS:
        kwargs: Dict[str, Any] = {}
        if spec.revision:
            kwargs["revision"] = spec.revision
        # An explicitly supplied asset path is an offline contract.  Do not
        # silently fall back to a Hub download when it is incomplete.
        if spec.local_model_path:
            kwargs["local_files_only"] = True
            kwargs["trust_remote_code"] = False
        model = embedding_provider._load_sentence_transformer_class("bench")(spec.local_model_path or spec.model, **kwargs)
        if spec.max_seq_length:
            model.max_seq_length = int(spec.max_seq_length)
        _MODELS[key] = model
    return _MODELS[key]


def _texts_hash(texts: Sequence[str]) -> str:
    h = hashlib.sha256()
    for text in texts:
        h.update(text.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def encode(
    spec: EmbeddingSpec, texts: Sequence[str], kind: str, cache_dir: Path
) -> Tuple[np.ndarray, Optional[Dict[str, float]]]:
    """L2-normalized float32 matrix; stats are None when served from cache."""
    if kind not in ("query", "document"):
        raise ValueError("kind must be 'query' or 'document'")
    path = Path(cache_dir) / "emb" / f"{spec.key()}_{kind}_{_texts_hash(texts)}.npy"
    if path.exists():
        return np.load(path), None
    query_prefix, document_prefix = spec.prefixes()
    prefix = query_prefix if kind == "query" else document_prefix
    model = load_model(spec)
    started = time.perf_counter()
    vectors = model.encode(
        [prefix + t for t in texts], batch_size=spec.batch_size, normalize_embeddings=True, show_progress_bar=False
    )
    seconds = time.perf_counter() - started
    matrix = np.asarray(vectors, dtype=np.float32)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(path, matrix)
    return matrix, {"texts": float(len(texts)), "seconds": seconds, "texts_per_second": len(texts) / max(seconds, 1e-9)}


def single_query_latency(spec: EmbeddingSpec, queries: Sequence[str], sample: int = 50) -> Dict[str, float]:
    """Online latency: one query per call, as at serving time (after warm-up)."""
    if not queries:
        return latency_summary([])
    model = load_model(spec)
    prefix = spec.prefixes()[0]
    model.encode([prefix + queries[0]], normalize_embeddings=True, show_progress_bar=False)
    timings = []
    for query in list(queries)[:sample]:
        started = time.perf_counter()
        model.encode([prefix + query], normalize_embeddings=True, show_progress_bar=False)
        timings.append((time.perf_counter() - started) * 1000.0)
    return latency_summary(timings)


def token_length_stats(spec: EmbeddingSpec, texts: Sequence[str], kind: str = "document") -> Dict[str, float]:
    """Token counts including the prefix, to size max_seq_length and chunks."""
    model = load_model(spec)
    prefix = spec.prefixes()[0 if kind == "query" else 1]
    lengths = sorted(len(model.tokenizer(prefix + t)["input_ids"]) for t in texts)
    limit = int(getattr(model, "max_seq_length", 0) or 0)
    return {
        "texts": float(len(lengths)),
        "p50": percentile(lengths, 0.50),
        "p95": percentile(lengths, 0.95),
        "p99": percentile(lengths, 0.99),
        "max": float(lengths[-1]) if lengths else 0.0,
        "max_seq_length": float(limit),
        "truncated_share": (sum(1 for n in lengths if limit and n > limit) / len(lengths)) if lengths else 0.0,
    }


def _renormalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return (matrix / np.maximum(norms, 1e-12)).astype(np.float32)


class Projection:
    """Dimension reduction fitted on corpus vectors and applied to queries too."""

    def __init__(self, spec: str, corpus: np.ndarray):
        self.spec = spec
        self.kind, _, size = spec.partition(":")
        self.size = int(size) if size else corpus.shape[1]
        if self.kind not in ("none", "truncate", "pca"):
            raise ValueError(f"unknown dimension spec '{spec}' (none, truncate:N, pca:N)")
        if not 0 < self.size <= corpus.shape[1]:
            raise ValueError(f"dimension {self.size} must be within 1..{corpus.shape[1]}")
        self.mean = None
        self.components = None
        if self.kind == "pca":
            self.mean = corpus.mean(axis=0)
            centered = corpus - self.mean
            covariance = centered.T @ centered / max(1, corpus.shape[0] - 1)
            eigenvalues, eigenvectors = np.linalg.eigh(covariance)
            order = np.argsort(eigenvalues)[::-1][: self.size]
            self.components = eigenvectors[:, order].astype(np.float32)
            self.explained_variance = float(eigenvalues[order].sum() / max(eigenvalues.sum(), 1e-12))

    def apply(self, matrix: np.ndarray) -> np.ndarray:
        if self.kind == "none":
            return matrix
        if self.kind == "truncate":
            return _renormalize(matrix[:, : self.size])
        return _renormalize((matrix - self.mean) @ self.components)


class DenseIndex:
    """Exact search on float32, int8 (per-dimension min/max) or binary codes."""

    def __init__(self, vectors: np.ndarray, tenants: Sequence[str], quantization: str = "float32", rescore_oversample: int = 1):
        if quantization not in ("float32", "int8", "binary"):
            raise ValueError("quantization must be float32, int8 or binary")
        self.vectors = vectors.astype(np.float32)
        self.tenants = np.asarray(tenants)
        self.quantization = quantization
        self.rescore_oversample = max(1, int(rescore_oversample))
        n, d = self.vectors.shape
        if quantization == "int8":
            # Calibrated on the corpus itself, as recommended for scalar int8.
            low, high = self.vectors.min(axis=0), self.vectors.max(axis=0)
            scale = np.maximum((high - low) / 255.0, 1e-12)
            codes = np.round((self.vectors - low) / scale).astype(np.uint8)
            self.approx = (codes.astype(np.float32) * scale + low).astype(np.float32)
            self.memory_bytes = n * d
        elif quantization == "binary":
            self.approx = np.where(self.vectors > 0, 1.0, -1.0).astype(np.float32)
            self.memory_bytes = n * ((d + 7) // 8)
        else:
            self.approx = self.vectors
            self.memory_bytes = n * d * 4

    def search(self, query: np.ndarray, k: int, tenant_id: Optional[str] = None) -> List[Tuple[int, float]]:
        probe = np.where(query > 0, 1.0, -1.0).astype(np.float32) if self.quantization == "binary" else query
        scores = self.approx @ probe
        if tenant_id is not None:
            scores = np.where(self.tenants == tenant_id, scores, -np.inf)
        valid = int(np.isfinite(scores).sum())
        if valid == 0 or k <= 0:
            return []
        rescore = self.quantization != "float32" and self.rescore_oversample > 1
        pool = min(valid, k * self.rescore_oversample if rescore else k)
        candidates = np.argpartition(-scores, pool - 1)[:pool]
        if rescore:
            exact = self.vectors[candidates] @ query
            order = candidates[np.argsort(-exact, kind="stable")][:k]
            return [(int(i), float(self.vectors[i] @ query)) for i in order]
        order = candidates[np.argsort(-scores[candidates], kind="stable")][:k]
        return [(int(i), float(scores[i])) for i in order if np.isfinite(scores[i])]

    def describe(self) -> Dict[str, Any]:
        return {
            "type": "exact",
            "quantization": self.quantization,
            "rescore_oversample": self.rescore_oversample,
            "dimensions": int(self.vectors.shape[1]),
            "memory_bytes": int(self.memory_bytes),
        }
