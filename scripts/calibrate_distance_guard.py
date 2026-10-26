"""Calibrate the guard's vector-distance thresholds for the active embedding
model (stage 14).

RAG_SOFT_DIST_* / RAG_HARD_MAX_DIST are cosine distances (1 - similarity) of
the best vector hit. Their defaults (0.65 / 0.95) were tuned on runs with
paraphrase-multilingual-MiniLM-L12-v2 (answerable questions: median best
distance about 0.33). Other models place distances differently: ruri-v3 and
e5-style models give unrelated passages much higher similarity, so the same
numbers would almost never abstain. Calibrate after switching models.

Input: an evaluation JSONL, one question per line, with "question" (or
"query") and either "answerable" (true/false) or "expected_used_fallback"
(true = must abstain), as in eval/cases/. Uses the configured embedding model
and vector store (served collection unless --collection).

  python scripts/calibrate_distance_guard.py --cases eval/cases/<set>.jsonl [--collection NAME]
      [--keep-answerable 0.95] [--output runs/calibration/distance_guard.json]

Output: the best-distance distribution of answerable and unanswerable
questions, the recommended RAG_SOFT_DIST_OTHER / RAG_HARD_MAX_DIST and the
answer / abstain rates they give on this set. Review them before applying;
they are only as good as the evaluation set.
"""
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
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


def load_cases(path: Path) -> List[Tuple[str, bool, str]]:
    """(question, answerable, tenant_id) per line."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        question = str(row.get("question") or row.get("query") or "").strip()
        if not question:
            continue
        if "answerable" in row:
            answerable = bool(row["answerable"])
        elif "expected_used_fallback" in row:
            answerable = not bool(row["expected_used_fallback"])
        else:
            continue
        out.append((question, answerable, str(row.get("tenant_id") or "default")))
    return out


def _quantile(sorted_values: Sequence[float], q: float) -> Optional[float]:
    if not sorted_values:
        return None
    pos = min(len(sorted_values) - 1, max(0, math.ceil(q * len(sorted_values)) - 1))
    return round(float(sorted_values[pos]), 4)


def summarize(values: Sequence[float]) -> Dict[str, Any]:
    ordered = sorted(values)
    return {"count": len(ordered), **{f"p{int(q * 100)}": _quantile(ordered, q) for q in (0.05, 0.25, 0.5, 0.75, 0.95)}}


def recommend(answerable: Sequence[float], unanswerable: Sequence[float], *, keep_answerable: float = 0.95
              ) -> Dict[str, Any]:
    """Soft threshold: the smallest distance that still answers keep_answerable
    of the answerable questions (they abstain above it). Hard threshold: above
    every answerable question seen, so only clear misses are cut hard."""
    ans = sorted(answerable)
    if not ans:
        return {"error": "no answerable questions with a vector hit"}
    soft = _quantile(ans, keep_answerable)
    hard = round(max(ans[-1], soft) + 0.02, 4)

    def rates(threshold: float) -> Dict[str, Optional[float]]:
        return {
            "answerable_answered": round(sum(1 for d in ans if d <= threshold) / len(ans), 3),
            "unanswerable_abstained": (round(sum(1 for d in unanswerable if d > threshold) / len(unanswerable), 3)
                                       if unanswerable else None),
        }

    # How well distance alone separates the two groups on this set.
    best = None
    for threshold in sorted(set(ans) | set(unanswerable)):
        r = rates(threshold)
        score = r["answerable_answered"] + (r["unanswerable_abstained"] or 0.0)
        if best is None or score > best[0]:
            best = (score, threshold, r)
    return {
        "RAG_SOFT_DIST_OTHER": soft,
        "RAG_HARD_MAX_DIST": hard,
        "at_soft": rates(soft),
        "best_separation": {"threshold": round(best[1], 4), **best[2]} if best else None,
        "note": "abstention also uses keyword evidence, salient terms and too_general; distance is one signal",
    }


def best_distances(cases, *, collection: Optional[str], top_k: int) -> List[Tuple[bool, Optional[float]]]:
    from rag_core import retrieval

    out = []
    for question, answerable, tenant in cases:
        hits = retrieval.vector_retrieve(question, None, top_k=top_k, tenant_id=tenant, collection_name=collection,
                                         create_collection_if_missing=False)
        distances = [float(h.metadata.get("vector_distance")) for h in hits if h.metadata.get("vector_distance") is not None]
        out.append((answerable, min(distances) if distances else None))
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cases", type=Path, required=True)
    ap.add_argument("--collection", default=None)
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--keep-answerable", type=float, default=0.95)
    ap.add_argument("--output", type=Path, default=Path("runs/calibration/distance_guard.json"))
    args = ap.parse_args(argv)

    from rag_core import embedding_provider

    cases = load_cases(args.cases)
    if not cases:
        raise SystemExit("no usable cases (need question and answerable / expected_used_fallback)")
    measured = best_distances(cases, collection=args.collection, top_k=args.top_k)
    ans = [d for a, d in measured if a and d is not None]
    unans = [d for a, d in measured if not a and d is not None]
    report = {
        "embedding": dict(embedding_provider.active_fingerprint()),
        "cases": len(cases), "without_vector_hit": sum(1 for _, d in measured if d is None),
        "answerable": summarize(ans), "unanswerable": summarize(unans),
        "recommendation": recommend(ans, unans, keep_answerable=args.keep_answerable),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
