"""Compare configurations of one bench run.

compare(): every candidate against a baseline on the same queries:
  paired cluster-bootstrap CI of the metric difference, exact McNemar on
  hit@k, sign-flip permutation p, Holm correction across candidates, and a
  non-inferiority verdict for the given margin.
pareto(): configurations not dominated on quality, p95 latency and memory.
calibrate(): abstention thresholds (isotonic, cost, conformal risk) and the
  fused-margin threshold for skipping the reranker.
Fit thresholds on a dev run and confirm them on a separate test run; the
in-sample ECE reported here is optimistic by construction.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from eval.bench import calibration
from eval.bench.stats import holm, mcnemar_from_hits, non_inferior, paired_bootstrap_diff, pareto_front, sign_flip_test


def load_summaries(run_dir: str | Path) -> List[Dict[str, Any]]:
    path = Path(run_dir) / "summary.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_per_query(run_dir: str | Path, config_id: str) -> Dict[str, Dict[str, Any]]:
    path = Path(run_dir) / "per_query" / f"{config_id}.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"no per-query results for config {config_id} in {run_dir}")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {row["query_id"]: row for row in rows}


def _k_of(metric: str) -> str:
    return metric.split("@", 1)[1] if "@" in metric else "10"


def compare(
    run_dir: str | Path,
    baseline_id: str,
    *,
    metric: str = "recall@10",
    margin: float = 0.02,
    candidates: Optional[Sequence[str]] = None,
    resamples: int = 2000,
    alpha: float = 0.05,
    seed: int = 0,
) -> Dict[str, Any]:
    summaries = {s["config_id"]: s for s in load_summaries(run_dir)}
    if baseline_id not in summaries:
        raise KeyError(f"baseline {baseline_id} not found in {run_dir}")
    base = load_per_query(run_dir, baseline_id)
    hit_metric = f"hit@{_k_of(metric)}"
    results: Dict[str, Dict[str, Any]] = {}
    pvalues: Dict[str, float] = {}
    for cid in candidates or [c for c in summaries if c != baseline_id]:
        rows = load_per_query(run_dir, cid)
        common = [q for q, r in base.items() if r.get("metrics") and rows.get(q, {}).get("metrics")]
        before = [base[q]["metrics"][metric] for q in common]
        after = [rows[q]["metrics"][metric] for q in common]
        groups = [base[q]["group"] for q in common]
        diff = paired_bootstrap_diff(before, after, groups, resamples=resamples, alpha=alpha, seed=seed)
        permutation_p = sign_flip_test([b - a for a, b in zip(before, after)], seed=seed)
        results[cid] = {
            "config": summaries[cid]["config"],
            "metric": metric,
            "baseline_mean": sum(before) / len(before) if before else None,
            "candidate_mean": sum(after) / len(after) if after else None,
            **diff,
            "mcnemar": mcnemar_from_hits(
                [base[q]["metrics"][hit_metric] for q in common], [rows[q]["metrics"][hit_metric] for q in common]
            ),
            "permutation_p": permutation_p,
            "non_inferior": non_inferior(diff["low"], margin),
            "total_p95_ms": summaries[cid].get("total_p95_ms"),
            "memory_bytes": summaries[cid].get("memory_bytes"),
        }
        pvalues[cid] = permutation_p
    adjusted = holm(pvalues, alpha)
    for cid, result in results.items():
        result["holm"] = adjusted[cid]
    return {"baseline": baseline_id, "metric": metric, "margin": margin, "alpha": alpha, "results": results}


def pareto(run_dir: str | Path, *, quality: str = "recall@10") -> List[Dict[str, Any]]:
    rows = []
    for s in load_summaries(run_dir):
        overall = s.get("metrics", {}).get("all", {})
        if quality not in overall:
            continue
        rows.append(
            {
                "config_id": s["config_id"],
                "quality": overall[quality],
                "latency_p95_ms": s.get("total_p95_ms") or 0.0,
                "memory_bytes": s.get("memory_bytes") or 0,
                "config": s["config"],
            }
        )
    front = pareto_front(rows, maximize=["quality"], minimize=["latency_p95_ms", "memory_bytes"])
    return sorted(front, key=lambda r: -r["quality"])


def calibrate(
    run_dir: str | Path,
    config_id: str,
    *,
    alpha: float = 0.05,
    cost_wrong: float = 10.0,
    cost_abstain: float = 1.0,
    base_config_id: Optional[str] = None,
    max_skip_loss: float = 0.005,
    k: int = 10,
) -> Dict[str, Any]:
    rows = load_per_query(run_dir, config_id)
    scored = [r for r in rows.values() if r.get("top1_score") is not None]
    scores = [float(r["top1_score"]) for r in scored]
    correct = [1 if (r["answerable"] and r["top1_relevant"]) else 0 for r in scored]
    wrong = [1 - c for c in correct]
    model = calibration.isotonic_fit(scores, correct)
    probs = [calibration.isotonic_predict(model, s) for s in scores]
    p_threshold = calibration.cost_threshold(cost_wrong, cost_abstain)
    eligible = [s for s, p in zip(scores, probs) if p >= p_threshold]
    out: Dict[str, Any] = {
        "config_id": config_id,
        "queries": len(scored),
        "answerable_share": sum(1 for r in scored if r["answerable"]) / max(1, len(scored)),
        "ece_isotonic_in_sample": calibration.expected_calibration_error(probs, correct),
        "cost_probability_threshold": p_threshold,
        "cost_score_threshold": min(eligible) if eligible else None,
        "conformal": calibration.conformal_risk_threshold(scores, wrong, alpha) if scores else None,
        "aurc": calibration.risk_coverage(scores, wrong)["aurc"] if scores else None,
    }
    if base_config_id:
        base = load_per_query(run_dir, base_config_id)
        hit = f"hit@{k}"
        common = [
            q for q, r in rows.items()
            if r.get("metrics") and base.get(q, {}).get("metrics") and r.get("fused_margin") is not None
        ]
        out["rerank_skip"] = calibration.margin_skip_analysis(
            [rows[q]["fused_margin"] for q in common],
            [base[q]["metrics"][hit] for q in common],
            [rows[q]["metrics"][hit] for q in common],
            max_skip_loss,
        )
        out["rerank_skip"]["queries"] = len(common)
    return out


def compare_markdown(result: Dict[str, Any]) -> str:
    lines = [
        f"baseline: {result['baseline']}  metric: {result['metric']}  margin: {result['margin']}",
        "",
        "| config | candidate | diff | 95% CI | McNemar p | perm p | Holm p | non-inferior | p95 ms | memory MB |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    ordered = sorted(result["results"].items(), key=lambda kv: -(kv[1]["diff"] or 0))
    for cid, r in ordered:
        memory = (r["memory_bytes"] or 0) / 1e6
        lines.append(
            f"| {cid} | {r['candidate_mean']:.4f} | {r['diff']:+.4f} | [{r['low']:+.4f}, {r['high']:+.4f}] | "
            f"{r['mcnemar']['p_value']:.4g} | {r['permutation_p']:.4g} | {r['holm']['p_adjusted']:.4g} | "
            f"{'yes' if r['non_inferior'] else 'no'} | {r['total_p95_ms'] or 0:.1f} | {memory:.1f} |"
        )
    return "\n".join(lines)
