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

from eval.bench.report import load_per_query
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
) -> Dict[str, Any]:
    base = load_per_query(baseline_run, baseline_config)
    cand = load_per_query(candidate_run, candidate_config)
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
