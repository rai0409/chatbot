"""Inverted-index Okapi BM25.

rank_bm25.BM25Okapi.get_scores walks every document for every query term
(O(terms x documents) per query). Here each query term only touches the
documents in its postings list, so cost follows the matching documents, not
the corpus size. idf is Lucene's non-negative form
log(1 + (N - df + 0.5) / (df + 0.5)); rank_bm25 uses log((N - df + 0.5) /
(df + 0.5)) with an epsilon floor, so absolute scores differ slightly while
the ranking behaviour is the same family.
"""

from __future__ import annotations

import heapq
import math
from array import array
from collections import Counter
from typing import Dict, Iterable, List, Sequence, Tuple


class InvertedBM25:
    def __init__(self, corpus: Iterable[Sequence[str]] = (), *, k1: float = 1.5, b: float = 0.75):
        if k1 < 0:
            raise ValueError("k1 must be >= 0")
        if not 0.0 <= b <= 1.0:
            raise ValueError("b must be within [0, 1]")
        self.k1 = float(k1)
        self.b = float(b)
        self.doc_count = 0
        self.doc_lengths = array("I")
        # Stage 14: postings as two packed arrays per term (doc index, term
        # frequency): 8 bytes per posting instead of a tuple of two ints
        # (about 70 bytes); 200,000 chunks have tens of millions of postings.
        self.postings: Dict[str, Tuple[array, array]] = {}
        for doc in corpus:
            self.add(doc)
        self.finish()

    def add(self, doc: Sequence[str]) -> None:
        """Index one more document (its index is the current doc_count).

        Documents can be added one by one while reading a corpus, so its token
        lists never have to be held all at once; call finish() afterwards."""
        idx = self.doc_count
        self.doc_count += 1
        self.doc_lengths.append(len(doc))
        postings = self.postings
        for term, tf in Counter(doc).items():
            entry = postings.get(term)
            if entry is None:
                entry = postings[term] = (array("I"), array("I"))
            entry[0].append(idx)
            entry[1].append(tf)

    def finish(self) -> "InvertedBM25":
        """Corpus statistics (idf, length normalization) after the last add()."""
        self.avg_doc_length = sum(self.doc_lengths) / self.doc_count if self.doc_count else 0.0
        self.idf = {term: self._idf(len(entry[0])) for term, entry in self.postings.items()}
        # Per-document length normalization is query-independent; precompute it.
        if self.avg_doc_length > 0:
            self._norm = array("d", (
                self.k1 * (1.0 - self.b + self.b * length / self.avg_doc_length) for length in self.doc_lengths
            ))
        else:
            self._norm = array("d", [self.k1] * self.doc_count)
        return self

    def _idf(self, df: int) -> float:
        return math.log(1.0 + (self.doc_count - df + 0.5) / (df + 0.5))

    def score(self, query_terms: Iterable[str]) -> Dict[int, float]:
        """BM25 score per matching document index (documents with no query term are absent)."""
        scores: Dict[int, float] = {}
        norm = self._norm
        for term in dict.fromkeys(query_terms):
            entry = self.postings.get(term)
            if not entry:
                continue
            idf = self.idf[term]
            k1_plus_1 = self.k1 + 1.0
            for idx, tf in zip(entry[0], entry[1]):
                scores[idx] = scores.get(idx, 0.0) + idf * tf * k1_plus_1 / (tf + norm[idx])
        return scores

    def score_arrays(self, query_terms: Iterable[str]):
        """(document indexes, scores) as numpy arrays for the matching documents.

        Same scores as score(), vectorised per term (stage 14): a common term
        matches most of a 200,000-chunk corpus, which the per-posting Python
        loop of score() takes about a second for."""
        import numpy as np

        norm = np.frombuffer(self._norm, dtype=np.float64) if len(self._norm) else np.zeros(0)
        total = np.zeros(self.doc_count, dtype=np.float64)
        touched = np.zeros(self.doc_count, dtype=bool)
        k1_plus_1 = self.k1 + 1.0
        for term in dict.fromkeys(query_terms):
            entry = self.postings.get(term)
            if not entry:
                continue
            ids = np.frombuffer(entry[0], dtype=np.uint32)
            tfs = np.frombuffer(entry[1], dtype=np.uint32).astype(np.float64)
            # A term lists each document once, so plain fancy-index addition is exact.
            total[ids] += self.idf[term] * tfs * k1_plus_1 / (tfs + norm[ids])
            touched[ids] = True
        matched = np.flatnonzero(touched)
        return matched, total[matched]

    def top_k(self, query_terms: Iterable[str], k: int) -> List[Tuple[int, float]]:
        """Highest-scoring (doc index, score) pairs; ties broken by lower index."""
        scores = self.score(query_terms)
        return heapq.nlargest(max(0, k), scores.items(), key=lambda item: (item[1], -item[0]))

    def stats(self) -> Dict[str, float]:
        return {
            "documents": float(self.doc_count),
            "terms": float(len(self.postings)),
            "postings": float(sum(len(p[0]) for p in self.postings.values())),
            "avg_doc_length": self.avg_doc_length,
        }
