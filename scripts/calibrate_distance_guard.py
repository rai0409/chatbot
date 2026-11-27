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

2026-11-22: the distance measured is the guard's own evidence
(qa._guard_evidence over qa._build_retrieval_trace: preprocessing, augmented
query, synonym lane, multi-query, fusion), not a raw vector search, and every
threshold is recommended: RAG_SOFT_DIST_OTHER / _RESET / _CHANGE / _PROCEDURE
(per intent when the set has enough cases of it, else pooled) and
RAG_HARD_MAX_DIST.

Output (--output, default runs/calibration/distance_guard.json): a calibration
file {"identity": {provider, model, revision, prefix_scheme, dim,
max_seq_length}, "thresholds": {...}, "measured_at", "n_cases", ...}. Point
RAG_DIST_CALIBRATION_FILE at it: the guard uses its thresholds while the
active embedding identity matches (explicit environment values still win),
and /admin/health and the production guard report calibrated=false on any
identity mismatch. Review the numbers first; they are only as good as the
evaluation set.
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


def guard_evidence_distances(cases, *, collection: Optional[str], top_k: int) -> List[Dict[str, Any]]:
    """Best vector distance as the production guard sees it, one row per case.

    2026-11-22: this used to embed the raw question with vector_retrieve only.
    The guard (qa.guard_merged_top) reads qa._guard_evidence over the candidates
    of the full retrieval (_build_retrieval_trace: preprocessing, augmented
    query, synonym lane, multi-query, fusion, rerank), so that is measured."""
    from rag_core import qa

    rows: List[Dict[str, Any]] = []
    for question, answerable, tenant in cases:
        _trace, state, _started = qa._build_retrieval_trace(
            question, client=None, top_k=top_k, max_context_chars=8000, tenant_id=tenant, collection_name=collection,
        )
        evidence = qa._guard_evidence(state.retrieved)
        distance = evidence.get("best_vector_distance")
        rows.append({
            "answerable": answerable,
            "intent": state.intent,
            "distance": float(distance) if distance is not None else None,
        })
    return rows


def best_distances(cases, *, collection: Optional[str], top_k: int) -> List[Tuple[bool, Optional[float]]]:
    """(answerable, best guard-evidence distance) per case (kept for callers of the old helper)."""
    return [(row["answerable"], row["distance"])
            for row in guard_evidence_distances(cases, collection=collection, top_k=top_k)]


_INTENT_KEYS = {
    "other": "RAG_SOFT_DIST_OTHER",
    "reset": "RAG_SOFT_DIST_RESET",
    "change": "RAG_SOFT_DIST_CHANGE",
    "procedure": "RAG_SOFT_DIST_PROCEDURE",
}


def recommend_all(rows: Sequence[Dict[str, Any]], *, keep_answerable: float = 0.95, min_per_intent: int = 5,
                  procedure_delta: float = 0.03) -> Dict[str, Any]:
    """Every threshold the guard uses: one soft threshold per intent and the hard one.

    An intent with fewer than min_per_intent answerable cases gets the pooled
    soft threshold (marked "pooled" in sources); the hard threshold is above
    every answerable case of the set."""
    ans_all = [r["distance"] for r in rows if r["answerable"] and r["distance"] is not None]
    unans_all = [r["distance"] for r in rows if not r["answerable"] and r["distance"] is not None]
    pooled = recommend(ans_all, unans_all, keep_answerable=keep_answerable)
    if "error" in pooled:
        return {"error": pooled["error"]}
    thresholds: Dict[str, float] = {"RAG_HARD_MAX_DIST": float(pooled["RAG_HARD_MAX_DIST"])}
    sources: Dict[str, str] = {"RAG_HARD_MAX_DIST": "pooled"}
    per_intent: Dict[str, Any] = {}
    for intent, key in _INTENT_KEYS.items():
        ans = [r["distance"] for r in rows if r["answerable"] and r["intent"] == intent and r["distance"] is not None]
        unans = [r["distance"] for r in rows if not r["answerable"] and r["intent"] == intent and r["distance"] is not None]
        if len(ans) >= min_per_intent:
            rec = recommend(ans, unans, keep_answerable=keep_answerable)
            thresholds[key] = float(rec["RAG_SOFT_DIST_OTHER"])
            sources[key] = "intent"
            per_intent[intent] = {"answerable": len(ans), "unanswerable": len(unans), "at_soft": rec["at_soft"]}
        else:
            thresholds[key] = float(pooled["RAG_SOFT_DIST_OTHER"])
            sources[key] = "pooled"
            per_intent[intent] = {"answerable": len(ans), "unanswerable": len(unans), "note": "too few cases; pooled"}
        # A soft threshold above the hard one would never fire.
        thresholds[key] = min(thresholds[key], thresholds["RAG_HARD_MAX_DIST"])
    thresholds["RAG_HARD_MAX_DIST_PROCEDURE_DELTA"] = float(procedure_delta)
    sources["RAG_HARD_MAX_DIST_PROCEDURE_DELTA"] = "current_setting_not_measured"
    return {"thresholds": thresholds, "sources": sources, "pooled": pooled, "per_intent": per_intent}


def build_calibration_file(rows: Sequence[Dict[str, Any]], *, identity: Dict[str, Any], keep_answerable: float,
                           min_per_intent: int, procedure_delta: float, measured_at: Optional[str] = None
                           ) -> Dict[str, Any]:
    """The JSON that RAG_DIST_CALIBRATION_FILE points to (rag_core/qa.py reads identity and thresholds)."""
    from datetime import datetime, timezone

    rec = recommend_all(rows, keep_answerable=keep_answerable, min_per_intent=min_per_intent,
                        procedure_delta=procedure_delta)
    if "error" in rec:
        raise SystemExit(rec["error"])
    return {
        "schema": "distance_guard_calibration.v2",
        "identity": dict(identity),
        "thresholds": rec["thresholds"],
        "threshold_sources": rec["sources"],
        "measured_at": measured_at or datetime.now(timezone.utc).isoformat(),
        "n_cases": len(rows),
        "without_vector_hit": sum(1 for r in rows if r["distance"] is None),
        "evidence": "qa._guard_evidence over qa._build_retrieval_trace (production retrieval path)",
        "pooled": rec["pooled"],
        "per_intent": rec["per_intent"],
    }


def _collection_dim(collection: Optional[str]) -> Optional[int]:
    from rag_core import embedding_fingerprint, embedding_provider, store

    dim = embedding_provider.loaded_embedding_dim()
    if dim:
        return dim
    try:
        col = store.get_vectorstore(collection_name=collection, create_if_missing=False)
    except Exception as exc:  # noqa: BLE001 - offline helper; the identity is then written without dim
        print(f"warning: collection dimension unavailable ({type(exc).__name__}); identity.dim will be empty "
              "and the dimension check is skipped at run time", file=sys.stderr)
        return None
    meta = dict(getattr(col, "metadata", None) or {})
    try:
        return int(str(meta.get("embedding_dim") or "").strip())
    except ValueError:
        return embedding_fingerprint.embedding_dim_from_collection(col)


def main(argv: Optional[List[str]] = None) -> int:
    import config

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cases", type=Path, required=True)
    ap.add_argument("--collection", default=None)
    ap.add_argument("--top-k", type=int, default=int(getattr(config, "TOP_K", 20)))
    ap.add_argument("--keep-answerable", type=float, default=0.95)
    ap.add_argument("--min-per-intent", type=int, default=5)
    ap.add_argument("--output", type=Path, default=Path("runs/calibration/distance_guard.json"))
    args = ap.parse_args(argv)

    from rag_core import embedding_provider

    cases = load_cases(args.cases)
    if not cases:
        raise SystemExit("no usable cases (need question and answerable / expected_used_fallback)")
    rows = guard_evidence_distances(cases, collection=args.collection, top_k=args.top_k)
    identity = embedding_provider.embedding_identity(embedding_dim=_collection_dim(args.collection))
    report = build_calibration_file(
        rows, identity=identity, keep_answerable=args.keep_answerable, min_per_intent=args.min_per_intent,
        procedure_delta=float(getattr(config, "RAG_HARD_MAX_DIST_PROCEDURE_DELTA", 0.03)),
    )
    report["embedding"] = dict(embedding_provider.active_fingerprint())
    report["answerable"] = summarize([r["distance"] for r in rows if r["answerable"] and r["distance"] is not None])
    report["unanswerable"] = summarize([r["distance"] for r in rows if not r["answerable"] and r["distance"] is not None])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nSet RAG_DIST_CALIBRATION_FILE={args.output} to use these thresholds "
          "(explicit RAG_SOFT_DIST_* / RAG_HARD_MAX_DIST environment values still win).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
