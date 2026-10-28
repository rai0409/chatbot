"""Paired comparison of two bench runs (stage 4 baseline vs a candidate).

eval.bench compare works inside one run. A corpus change (re-ingestion after
stage 3, a new chunker, NORMALIZATION_PROFILE=index) needs two runs over the
same evaluation set; this module pairs their per-query results by query_id
and reports, overall and per slice:

  - mean of the metric in both runs and the paired cluster-bootstrap CI of
    the difference (clusters = query group, as in eval.bench compare)
  - exact McNemar on hit@k and a sign-flip permutation p
  - Holm correction across slices, and a non-inferiority verdict

Use evidence match mode (or doc) when the two corpora have different chunk
ids; chunk-id gold labels do not carry over a re-ingestion.

  python -m eval.bench.compare_runs \\
      --baseline-run runs/bench/<A> --baseline-config <id> \\
      --candidate-run runs/bench/<B> --candidate-config <id> \\
      --metric recall@10 --margin 0.02 [--markdown] [--out report.md]
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from eval.bench.report import load_per_query, load_summaries
from eval.bench.stats import holm, mcnemar_from_hits, non_inferior, paired_bootstrap_diff, sign_flip_test

OVERALL = "(all)"
_MIN_SLICE_QUERIES = 10


def _k_of(metric: str) -> str:
    return metric.split("@", 1)[1] if "@" in metric else "10"


def _paired(
    base: Dict[str, Dict[str, Any]],
    cand: Dict[str, Dict[str, Any]],
    query_ids: Sequence[str],
    *,
    metric: str,
    margin: float,
    resamples: int,
    alpha: float,
    seed: int,
) -> Dict[str, Any]:
    hit_metric = f"hit@{_k_of(metric)}"
    before = [float(base[q]["metrics"][metric]) for q in query_ids]
    after = [float(cand[q]["metrics"][metric]) for q in query_ids]
    groups = [base[q].get("group") for q in query_ids]
    diff = paired_bootstrap_diff(before, after, groups, resamples=resamples, alpha=alpha, seed=seed)
    return {
        "n": len(query_ids),
        "baseline_mean": sum(before) / len(before) if before else None,
        "candidate_mean": sum(after) / len(after) if after else None,
        **diff,
        "mcnemar": mcnemar_from_hits(
            [float(base[q]["metrics"].get(hit_metric, 0.0)) for q in query_ids],
            [float(cand[q]["metrics"].get(hit_metric, 0.0)) for q in query_ids],
        ),
        "permutation_p": sign_flip_test([a - b for b, a in zip(before, after)], seed=seed),
        "non_inferior": bool(before) and non_inferior(diff["low"], margin),
    }


_CONDITION_NAMES = {
    "eval_set_sha256": "same_eval_set",
    "split": "same_split",
    "queries": "same_queries",
    "match_mode": "same_match_mode",
    "generation_id": "same_generation",
    "corpus_sha256": "same_corpus",
    "embedding_profile": "same_embedding_profile",
    "tie_break": "same_tie_break",
}


def _read_run_json(run_dir: str | Path) -> Dict[str, Any]:
    try:
        return json.loads((Path(run_dir) / "run.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def run_conditions(run_dir: str | Path, config_id: str) -> Dict[str, Any]:
    """What a run was measured on. A value that older runs did not record is None (unknown)."""
    meta = _read_run_json(run_dir)
    try:
        summary = next((row for row in load_summaries(run_dir) if row.get("config_id") == config_id), {})
    except FileNotFoundError:
        summary = {}
    index = summary.get("index") or {}
    generation = (meta.get("generation") or {}).get("generation_id") or "(none)" if "generation" in meta else None
    return {
        "eval_set_sha256": meta.get("eval_set_sha256"),
        "split": meta.get("split"),
        "queries": meta.get("queries"),
        "match_mode": (meta.get("config") or {}).get("match_mode", "evidence") if meta else None,
        "generation_id": generation,
        "corpus_sha256": index.get("corpus_sha256"),
        "embedding_profile": index.get("embedding_profile"),
        "tie_break": meta.get("tie_break"),
    }


def compare_conditions(baseline: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    differences, unknown = [], []
    same: Dict[str, Optional[bool]] = {}
    for key, name in _CONDITION_NAMES.items():
        a, b = baseline.get(key), candidate.get(key)
        if a is None or b is None:
            unknown.append(key)
            same[name] = None
        else:
            same[name] = a == b
            if a != b:
                differences.append(key)
    return {
        **same,
        "differences": differences,
        "unknown": unknown,
        "comparable": not differences and not unknown,
        "baseline": baseline,
        "candidate": candidate,
    }


def _paired_changes(
    base: Dict[str, Dict[str, Any]], cand: Dict[str, Dict[str, Any]], common: Sequence[str], metric: str, *, limit: int = 200
) -> Dict[str, Any]:
    """The questions the candidate answers better / worse than the baseline on the metric."""
    improved = [q for q in common if cand[q]["metrics"][metric] > base[q]["metrics"][metric] + 1e-12]
    worsened = [q for q in common if cand[q]["metrics"][metric] < base[q]["metrics"][metric] - 1e-12]
    return {
        "metric": metric,
        "improved": {"count": len(improved), "query_ids": improved[:limit]},
        "worsened": {"count": len(worsened), "query_ids": worsened[:limit]},
        "unchanged": len(common) - len(improved) - len(worsened),
    }


def compare_runs(
    baseline_run: str | Path,
    baseline_config: str,
    candidate_run: str | Path,
    candidate_config: str,
    *,
    metric: str = "recall@10",
    margin: float = 0.02,
    resamples: int = 2000,
    alpha: float = 0.05,
    seed: int = 0,
    min_slice_queries: int = _MIN_SLICE_QUERIES,
    require_same_conditions: bool = False,
) -> Dict[str, Any]:
    base = load_per_query(baseline_run, baseline_config)
    cand = load_per_query(candidate_run, candidate_config)
    conditions = compare_conditions(run_conditions(baseline_run, baseline_config), run_conditions(candidate_run, candidate_config))
    if require_same_conditions and not conditions["comparable"]:
        raise ValueError(
            "the runs were not measured under the same conditions: "
            f"different {conditions['differences']}, unknown {conditions['unknown']}"
        )
    common = sorted(q for q, r in base.items() if r.get("metrics") and cand.get(q, {}).get("metrics"))
    only_baseline = sorted(set(base) - set(cand))
    only_candidate = sorted(set(cand) - set(base))

    slices: Dict[str, List[str]] = {OVERALL: common}
    for q in common:
        for name in base[q].get("slices") or []:
            slices.setdefault(str(name), []).append(q)

    results: Dict[str, Dict[str, Any]] = {}
    skipped: Dict[str, int] = {}
    for name, qids in slices.items():
        if name != OVERALL and len(qids) < min_slice_queries:
            skipped[name] = len(qids)
            continue
        results[name] = _paired(
            base, cand, qids, metric=metric, margin=margin, resamples=resamples, alpha=alpha, seed=seed
        )
    pvalues = {name: r["permutation_p"] for name, r in results.items() if not math.isnan(r["permutation_p"])}
    adjusted = holm(pvalues, alpha) if pvalues else {}
    for name, result in results.items():
        result["holm"] = adjusted.get(name)

    # Unanswerable queries (no metrics) are not paired here; abstention is
    # evaluated with `python -m eval.bench calibrate` on each run.
    return {
        "metric": metric,
        "margin": margin,
        "alpha": alpha,
        "baseline": {"run": str(baseline_run), "config_id": baseline_config},
        "candidate": {"run": str(candidate_run), "config_id": candidate_config},
        "paired_queries": len(common),
        "conditions": conditions,
        "paired_changes": _paired_changes(base, cand, common, metric),
        "only_in_baseline": len(only_baseline),
        "only_in_candidate": len(only_candidate),
        "slices_skipped_small": skipped,
        "results": results,
        "verdict": {
            "non_inferior_overall": bool(results.get(OVERALL, {}).get("non_inferior")),
            "non_inferior_all_slices": all(r.get("non_inferior") for r in results.values()),
        },
    }


def to_markdown(report: Dict[str, Any]) -> str:
    lines = [
        f"# {report['metric']}: candidate vs baseline (margin {report['margin']})",
        "",
        f"paired queries: {report['paired_queries']}  "
        f"(only baseline: {report['only_in_baseline']}, only candidate: {report['only_in_candidate']})",
        "",
        "| slice | n | baseline | candidate | diff | 95% CI | McNemar p | perm p (Holm) | non-inferior |",
        "|---|---:|---:|---:|---:|---|---:|---:|---|",
    ]
    for name, r in report["results"].items():
        holm_p = (r.get("holm") or {}).get("p_adjusted")
        holm_text = f"{holm_p:.4f}" if isinstance(holm_p, float) else "-"
        lines.append(
            f"| {name} | {int(r['n'])} | {r['baseline_mean']:.4f} | {r['candidate_mean']:.4f} | {r['diff']:+.4f} "
            f"| [{r['low']:+.4f}, {r['high']:+.4f}] | {r['mcnemar']['p_value']:.4f} | {holm_text} "
            f"| {'yes' if r['non_inferior'] else 'NO'} |"
        )
    if report["slices_skipped_small"]:
        lines += ["", f"slices with fewer than the minimum queries (not tested): {report['slices_skipped_small']}"]
    conditions = report.get("conditions")
    if conditions:
        lines += [
            "",
            f"same conditions: {conditions['comparable']} (different: {conditions['differences']}, unknown: {conditions['unknown']})",
        ]
    changes = report.get("paired_changes")
    if changes:
        lines += [
            "",
            f"improved: {changes['improved']['count']}  worsened: {changes['worsened']['count']}  "
            f"unchanged: {changes['unchanged']}  ({changes['metric']})",
        ]
    verdict = report["verdict"]
    lines += [
        "",
        f"overall non-inferior: {verdict['non_inferior_overall']} / every tested slice: {verdict['non_inferior_all_slices']}",
    ]
    return "\n".join(lines) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Paired comparison of two eval.bench runs.")
    parser.add_argument("--baseline-run", required=True)
    parser.add_argument("--baseline-config", required=True)
    parser.add_argument("--candidate-run", required=True)
    parser.add_argument("--candidate-config", required=True)
    parser.add_argument("--metric", default="recall@10")
    parser.add_argument("--margin", type=float, default=0.02)
    parser.add_argument("--resamples", type=int, default=2000)
    parser.add_argument("--min-slice-queries", type=int, default=_MIN_SLICE_QUERIES)
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument("--out", default=None)
    parser.add_argument(
        "--require-same-conditions",
        action="store_true",
        help="refuse unless both runs used the same evaluation set, corpus, embedding profile and generation",
    )
    args = parser.parse_args(argv)
    report = compare_runs(
        args.baseline_run,
        args.baseline_config,
        args.candidate_run,
        args.candidate_config,
        metric=args.metric,
        margin=args.margin,
        resamples=args.resamples,
        min_slice_queries=args.min_slice_queries,
        require_same_conditions=args.require_same_conditions,
    )
    text = to_markdown(report) if args.markdown else json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0 if report["verdict"]["non_inferior_overall"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
