"""Lexical lane for experiments, using the production tokenizers.

Tokens come from rag_core.retrieval._document_tokens / _query_tokens under a
temporary config override, so a sweep measures exactly what production
would index. Scoring uses InvertedBM25 with the swept k1/b. order
"production" reproduces keyword_retrieve's ranking key (quoted hit, id hit,
exact-term hits, then BM25); order "bm25" ranks by BM25 alone.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterator, List, Sequence

import config
from rag_core import ja_tokenizer, retrieval
from rag_core.bm25_index import InvertedBM25
from eval.bench.corpus import Chunk


@dataclass(frozen=True)
class LexicalSpec:
    tokenizer: str = "heuristic"  # heuristic | sudachi
    split_mode: str = "A"
    ngram: bool = True
    numeric_tokens: bool = False
    k1: float = 1.5
    b: float = 0.75
    order: str = "production"  # production | bm25


@contextmanager
def config_override(**values: Any) -> Iterator[None]:
    missing = object()
    previous = {key: getattr(config, key, missing) for key in values}
    for key, value in values.items():
        setattr(config, key, value)
    ja_tokenizer.reset_cache()
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is missing:
                delattr(config, key)
            else:
                setattr(config, key, value)
        ja_tokenizer.reset_cache()


@dataclass(frozen=True)
class LexicalHit:
    index: int
    bm25: float
    quoted_hits: int
    id_hits: int
    exact_hits: int


class LexicalIndex:
    def __init__(self, chunks: Sequence[Chunk], spec: LexicalSpec):
        if spec.order not in ("production", "bm25"):
            raise ValueError("lexical order must be 'production' or 'bm25'")
        self.spec = spec
        self.tenants = [chunk.tenant_id for chunk in chunks]
        started = time.perf_counter()
        with self._settings():
            tokenizer = ja_tokenizer.active_tokenizer_name()
            if tokenizer != spec.tokenizer:
                raise RuntimeError(f"tokenizer '{spec.tokenizer}' requested but '{tokenizer}' is active")
            corpus = [retrieval._document_tokens(chunk.text, tokenizer) for chunk in chunks]
        self.bm25 = InvertedBM25(corpus, k1=spec.k1, b=spec.b)
        self.norm_texts = [retrieval._normalize(chunk.text) for chunk in chunks]
        self.build_seconds = time.perf_counter() - started

    def _settings(self):
        return config_override(
            KEYWORD_TOKENIZER=self.spec.tokenizer,
            SUDACHI_SPLIT_MODE=self.spec.split_mode,
            KEYWORD_NGRAM_ENABLED=self.spec.ngram,
            KEYWORD_NUMERIC_TOKENS=self.spec.numeric_tokens,
        )

    def search(self, query: str, k: int, tenant_id: str) -> List[LexicalHit]:
        with self._settings():
            tokens = retrieval._query_tokens(query, self.spec.tokenizer)
        scores = self.bm25.score(tokens)
        exact_terms, quoted_terms, id_terms = retrieval._query_match_terms(query)
        hits = []
        for idx, score in scores.items():
            if self.tenants[idx] != tenant_id:
                continue
            text = self.norm_texts[idx]
            hits.append(
                LexicalHit(
                    idx,
                    score,
                    sum(1 for t in quoted_terms if t and t in text),
                    sum(1 for t in id_terms if t and t in text),
                    sum(1 for t in exact_terms if t and t in text),
                )
            )
        if self.spec.order == "production":
            key = lambda h: (h.quoted_hits > 0, h.id_hits > 0, h.exact_hits, h.bm25, -h.index)
        else:
            key = lambda h: (h.bm25, -h.index)
        hits.sort(key=key, reverse=True)
        return hits[:k]

    def describe(self) -> Dict[str, Any]:
        return dict(asdict(self.spec), build_seconds=self.build_seconds, **self.bm25.stats())
