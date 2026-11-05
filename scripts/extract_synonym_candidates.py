from __future__ import annotations
# --- bootstrap: add repo root to sys.path for script execution ---
import sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# --- end bootstrap ---

"""Synonym candidates from the keyword corpus, for human review (N8).

Methods
  normalized_form  surfaces that Sudachi maps to the same normalized form
                   (サーバ / サーバー, 引越し / 引っ越し). Needs requirements-ja.
  embedding        nearest neighbours among frequent nouns by the embedding
                   model (cosine >= --min-cosine). Optional: --embedding-neighbors
Co-occurrence is not used: words that co-occur are related, not synonyms.

Output: JSONL, one candidate group per line, status "candidate". Review the
file (set status to "approved" or "rejected"), then apply approved rows with
scripts/apply_synonym_candidates.py. Candidates are never used for search
before approval.

  python scripts/extract_synonym_candidates.py --corpus <chunks.jsonl> --out runs/synonyms/candidates.jsonl
"""

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import config
from rag_core.ja_text import normalize_for_index

_NOUN = "名詞"
_MIN_SURFACE_CHARS = 2


def _iter_texts(path: Path) -> Iterable[str]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = str(row.get("searchable_text") or row.get("text") or "")
            if text:
                yield text


def _sudachi():
    from rag_core import ja_tokenizer

    return ja_tokenizer._get_sudachi()


def normalized_form_groups(texts: Iterable[str], *, min_count: int = 2) -> List[Dict[str, Any]]:
    tokenizer = _sudachi()
    surfaces: Dict[str, Counter] = defaultdict(Counter)
    for text in texts:
        for morpheme in tokenizer.tokenize(normalize_for_index(text)[:4000]):
            pos = morpheme.part_of_speech()
            if not pos or pos[0] != _NOUN:
                continue
            surface = morpheme.surface().strip()
            if len(surface) < _MIN_SURFACE_CHARS:
                continue
            surfaces[str(morpheme.normalized_form())][surface] += 1
    groups = []
    for normalized, counter in surfaces.items():
        variants = [s for s, c in counter.most_common() if c >= min_count]
        if len(variants) >= 2:
            groups.append(
                {
                    "method": "normalized_form",
                    "canonical": variants[0],
                    "synonyms": variants[1:],
                    "evidence": {"normalized_form": normalized, "counts": dict(counter.most_common(10))},
                }
            )
    return groups


def frequent_nouns(texts: Iterable[str], *, top: int) -> List[Tuple[str, int]]:
    tokenizer = _sudachi()
    counts: Counter = Counter()
    for text in texts:
        for morpheme in tokenizer.tokenize(normalize_for_index(text)[:4000]):
            pos = morpheme.part_of_speech()
            if pos and pos[0] == _NOUN and len(morpheme.surface()) >= _MIN_SURFACE_CHARS:
                counts[str(morpheme.normalized_form())] += 1
    return counts.most_common(top)


def embedding_neighbour_groups(terms: Sequence[str], *, min_cosine: float, client=None) -> List[Dict[str, Any]]:
    import numpy as np

    from rag_core import embedder

    if len(terms) < 2:
        return []
    vectors = np.asarray(embedder.embed_documents(list(terms), client=client), dtype=np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-12
    sims = vectors @ vectors.T
    groups = []
    seen = set()
    for i, term in enumerate(terms):
        neighbours = [
            (float(sims[i, j]), terms[j]) for j in range(len(terms)) if j != i and sims[i, j] >= min_cosine
        ]
        if not neighbours:
            continue
        key = frozenset([term, *(t for _, t in neighbours)])
        if key in seen:
            continue
        seen.add(key)
        neighbours.sort(reverse=True)
        groups.append(
            {
                "method": "embedding",
                "canonical": term,
                "synonyms": [t for _, t in neighbours[:5]],
                "evidence": {"cosine": {t: round(s, 4) for s, t in neighbours[:5]}},
            }
        )
    return groups


def _candidate_id(group: Dict[str, Any]) -> str:
    payload = json.dumps([group["method"], group["canonical"], sorted(group["synonyms"])], ensure_ascii=False)
    return "syn_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Extract synonym candidates for review.")
    parser.add_argument("--corpus", default=str(config.CHUNKS_JSONL_PATH))
    parser.add_argument("--out", required=True)
    parser.add_argument("--min-count", type=int, default=2)
    parser.add_argument("--embedding-neighbors", action="store_true")
    parser.add_argument("--top-terms", type=int, default=2000)
    parser.add_argument("--min-cosine", type=float, default=0.92)
    args = parser.parse_args(argv)

    corpus = Path(args.corpus)
    texts = list(_iter_texts(corpus))
    try:
        groups = normalized_form_groups(texts, min_count=args.min_count)
        if args.embedding_neighbors:
            terms = [t for t, _ in frequent_nouns(texts, top=args.top_terms)]
            groups.extend(embedding_neighbour_groups(terms, min_cosine=args.min_cosine))
    except RuntimeError as exc:
        print(f"error: {exc} (install requirements-ja.txt)", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for group in groups:
            row = {"candidate_id": _candidate_id(group), "status": "candidate", **group}
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps({"corpus": str(corpus), "texts": len(texts), "candidates": len(groups), "out": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
