"""Real-vector guard threshold calibration (Prompt017).

Measures best vector distances for answerable vs abstain-labeled eval cases
against a dedicated, fingerprint-stamped collection, sweeps candidate hard
thresholds, and recommends RAG_MAX_DISTANCE / RAG_SOFT_DIST_* values with the
measured trade-off. Nothing is changed automatically: this script only writes
a JSON + Markdown report under runs/eval/.

Methodology notes (stated in the report):
- best_distance models the guard's hard/soft *vector* checks only
  (rag_core/qa.py guard_merged_top). Keyword-only signals (too_general,
  salient_mismatch, weak_keyword_evidence) are independent of these
  thresholds, so end-to-end behavior must additionally be verified with
  eval.runner --real-vector before/after any change.
- The query embedded per case is the raw case query (the pipeline may also
  retrieve with an augmented query; raw-query distance is the conservative
  signal the guard sees on the primary pass).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Sequence

SCHEMA_VERSION = "guard_distance_calibration.v1"

DEFAULT_SWEEP_START = 0.50
DEFAULT_SWEEP_STOP = 1.00
DEFAULT_SWEEP_STEP = 0.05


def sweep_thresholds(start: float = DEFAULT_SWEEP_START, stop: float = DEFAULT_SWEEP_STOP, step: float = DEFAULT_SWEEP_STEP, extra: Sequence[float] = ()) -> List[float]:
    values = []
    t = start
    while t <= stop + 1e-9:
        values.append(round(t, 4))
        t += step
    for x in extra:
        x = round(float(x), 4)
        if x not in values:
            values.append(x)
    return sorted(set(values))


def summarize_distances(values: Sequence[float]) -> Dict[str, float | int | None]:
    if not values:
        return {"n": 0, "min": None, "p10": None, "p25": None, "p50": None, "p75": None, "p90": None, "p95": None, "max": None}
    ordered = sorted(float(v) for v in values)

    def pct(p: float) -> float:
        # Nearest-rank percentile: deterministic, no interpolation surprises.
        rank = max(1, round(p / 100.0 * len(ordered)))
        return round(ordered[min(rank, len(ordered)) - 1], 4)

    return {
        "n": len(ordered),
        "min": round(ordered[0], 4),
        "p10": pct(10),
        "p25": pct(25),
        "p50": pct(50),
        "p75": pct(75),
        "p90": pct(90),
        "p95": pct(95),
        "max": round(ordered[-1], 4),
    }


def threshold_sweep(
    answerable: Sequence[float],
    abstain: Sequence[float],
    thresholds: Sequence[float],
) -> List[Dict[str, float | int]]:
    rows = []
    for t in thresholds:
        false_abstain = sum(1 for d in answerable if d > t)
        false_answer = sum(1 for d in abstain if d <= t)
        rows.append(
            {
                "threshold": round(float(t), 4),
                "false_abstain_count": false_abstain,
                "false_abstain_rate": round(false_abstain / len(answerable), 4) if answerable else None,
                "false_answer_count": false_answer,
                "false_answer_rate": round(false_answer / len(abstain), 4) if abstain else None,
            }
        )
    return rows


def recommend_thresholds(
    answerable: Sequence[float],
    abstain: Sequence[float],
    *,
    current: Dict[str, float],
) -> Dict[str, object]:
    """Hard threshold: smallest sweep point blocking zero answerable cases.

    Soft thresholds are friction (fallback for weak evidence), not a hard
    block: recommend the answerable p95 (rounded up to the grid) so soft
    guards stop firing on the answerable bulk, intent deltas preserved
    relative to the current defaults.
    """
    ans_stats = summarize_distances(answerable)
    abs_stats = summarize_distances(abstain)
    separated = bool(
        answerable
        and abstain
        and ans_stats["max"] is not None
        and abs_stats["min"] is not None
        and ans_stats["max"] < abs_stats["min"]
    )
    grid = sweep_thresholds()
    hard = next((t for t in grid if all(d <= t for d in answerable)), current["hard"])
    # Snap soft base to the grid point at/above answerable p95.
    p95 = ans_stats["p95"] if ans_stats["p95"] is not None else current["soft_other"]
    soft_other = next((t for t in grid if t >= p95), current["soft_other"])
    # Preserve the current intent ladder spacing relative to soft_other.
    ladder = {
        "soft_reset": current["soft_reset"] - current["soft_other"],
        "soft_change": current["soft_change"] - current["soft_other"],
    }
    recommendation = {
        "hard": round(hard, 4),
        "soft_other": round(soft_other, 4),
        "soft_reset": round(min(hard, soft_other + ladder["soft_reset"]), 4),
        "soft_change": round(min(hard, soft_other + ladder["soft_change"]), 4),
    }
    sweep_at_hard = threshold_sweep(answerable, abstain, [recommendation["hard"]])[0]
    unambiguous = bool(
        answerable
        and abstain
        and sweep_at_hard["false_abstain_count"] == 0
        and separated
    )
    return {
        "recommended": recommendation,
        "current": dict(current),
        "separated": separated,
        "answerable": ans_stats,
        "abstain": abs_stats,
        "sweep_at_recommended_hard": sweep_at_hard,
        "unambiguous": unambiguous,
        "rationale": (
            "hard = smallest grid threshold with zero answerable cases above it "
            "(hard guard is a backstop, it must not block answerable queries); "
            "soft_other = grid point at/above the answerable p95 so soft guards "
            "stop firing on the answerable bulk; intent ladder spacing preserved. "
            f"Class separation (answerable max {ans_stats['max']} < abstain min {abs_stats['min']}): {separated}."
        ),
    }


def build_report(
    *,
    per_case: List[Dict[str, object]],
    collection: str,
    fingerprint: Dict[str, str] | None,
    cases_path: str,
    top_k: int,
    current: Dict[str, float],
) -> Dict[str, object]:
    answerable = [float(r["best_distance"]) for r in per_case if r["label"] == "answerable" and r["best_distance"] is not None]
    abstain = [float(r["best_distance"]) for r in per_case if r["label"] == "abstain" and r["best_distance"] is not None]
    thresholds = sweep_thresholds(extra=list(current.values()))
    return {
        "schema_version": SCHEMA_VERSION,
        "collection": collection,
        "embedding_fingerprint": fingerprint,
        "cases_path": cases_path,
        "top_k": top_k,
        "classes": {
            "answerable": summarize_distances(answerable),
            "abstain": summarize_distances(abstain),
        },
        "sweep": threshold_sweep(answerable, abstain, thresholds),
        "recommendation": recommend_thresholds(answerable, abstain, current=current),
        "per_case": per_case,
    }


def render_markdown(report: Dict[str, object]) -> str:
    rec = report["recommendation"]
    lines = [
        "# Guard Distance Calibration Report",
        "",
        f"- collection: `{report['collection']}`",
        f"- embedding fingerprint: `{report['embedding_fingerprint']}`",
        f"- cases: `{report['cases_path']}` (top_k={report['top_k']})",
        "",
        "## Distance distributions (best vector distance per case)",
        "",
        "| class | n | min | p10 | p25 | p50 | p75 | p90 | p95 | max |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for label in ("answerable", "abstain"):
        s = report["classes"][label]
        lines.append(
            f"| {label} | {s['n']} | {s['min']} | {s['p10']} | {s['p25']} | {s['p50']} | {s['p75']} | {s['p90']} | {s['p95']} | {s['max']} |"
        )
    lines += [
        "",
        f"Class separation: **{rec['separated']}**",
        "",
        "## Threshold sweep (hard-threshold model)",
        "",
        "| threshold | false_abstain | false_answer |",
        "|---|---|---|",
    ]
    for row in report["sweep"]:
        lines.append(
            f"| {row['threshold']} | {row['false_abstain_count']} ({row['false_abstain_rate']}) | {row['false_answer_count']} ({row['false_answer_rate']}) |"
        )
    lines += [
        "",
        "## Recommendation",
        "",
        f"- current: `{rec['current']}`",
        f"- recommended: `{rec['recommended']}`",
        f"- unambiguous: **{rec['unambiguous']}**",
        f"- rationale: {rec['rationale']}",
        "",
        "Note: the sweep models the vector hard threshold only. too_general /",
        "salient_mismatch / keyword-evidence guards are independent of these",
        "thresholds; verify end-to-end with eval.runner --real-vector.",
    ]
    return "\n".join(lines) + "\n"


def _measure(cases_path: Path, collection_name: str, top_k: int) -> tuple[List[Dict[str, object]], Dict[str, str] | None]:
    # Local imports: keep module import light for unit tests.
    from eval.runner import load_cases
    from rag_core import embedding_provider, store

    cases = load_cases(cases_path)
    labeled = []
    for case in cases:
        if case.expected_abstain or case.should_abstain:
            labeled.append((case, "abstain"))
        elif case.gold_chunk_ids:
            labeled.append((case, "answerable"))
    collection = store.get_vectorstore(collection_name, verify_embedding_fingerprint=True)
    fingerprint = store.collection_fingerprint(collection)
    queries = [case.query for case, _ in labeled]
    embeddings = embedding_provider.embed_queries(queries)
    per_case: List[Dict[str, object]] = []
    for (case, label), embedding in zip(labeled, embeddings):
        res = collection.query(query_embeddings=[embedding], n_results=top_k)
        distances = (res.get("distances") or [[]])[0]
        ids = (res.get("ids") or [[]])[0]
        best = round(float(min(distances)), 4) if distances else None
        gold_distance = None
        for chunk_id, dist in zip(ids, distances):
            if chunk_id in case.gold_chunk_ids:
                gold_distance = round(float(dist), 4)
                break
        per_case.append(
            {
                "case_id": case.case_id,
                "label": label,
                "best_distance": best,
                "gold_distance": gold_distance,
                "top_id": ids[0] if ids else None,
            }
        )
    return per_case, fingerprint


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure guard distance distributions on a dedicated real-vector collection.")
    parser.add_argument("--cases", default="eval/cases/real_corpus_cases.jsonl")
    parser.add_argument("--collection", required=True, help="dedicated eval collection (never the production collection)")
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--output-prefix", default="runs/eval/guard_distance_calibration")
    args = parser.parse_args(argv)

    import config

    current = {
        "hard": float(config.RAG_HARD_MAX_DIST),
        "soft_other": float(config.RAG_SOFT_DIST_OTHER),
        "soft_reset": float(config.RAG_SOFT_DIST_RESET),
        "soft_change": float(config.RAG_SOFT_DIST_CHANGE),
    }
    per_case, fingerprint = _measure(Path(args.cases), args.collection, args.top_k)
    report = build_report(
        per_case=per_case,
        collection=args.collection,
        fingerprint=fingerprint,
        cases_path=args.cases,
        top_k=args.top_k,
        current=current,
    )
    out_json = Path(f"{args.output_prefix}.json")
    out_md = Path(f"{args.output_prefix}.md")
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out_md.write_text(render_markdown(report), encoding="utf-8")

    rec = report["recommendation"]
    print(
        "Summary: "
        f"answerable_n={report['classes']['answerable']['n']} "
        f"abstain_n={report['classes']['abstain']['n']} "
        f"separated={rec['separated']} "
        f"unambiguous={rec['unambiguous']} "
        f"current={rec['current']} "
        f"recommended={rec['recommended']} "
        f"output={out_json}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
