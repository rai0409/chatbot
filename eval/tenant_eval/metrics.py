"""Summaries of evaluation rows (see runner.run_cases for the row fields)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from eval.bench.stats import bootstrap_ci, latency_summary
from eval.tenant_eval.stats import clopper_pearson_upper


def _rate(count: int, n: int) -> Optional[float]:
    return count / n if n else None


def is_error(row: Dict[str, Any]) -> bool:
    return "error" in row.get("failures", [])


def is_false_accept(row: Dict[str, Any]) -> bool:
    """The bot answered, and the answer fails the case. An abstention or an error is not a false accept."""
    return bool(row.get("answered")) and not row.get("passed") and not is_error(row)


RETRIEVAL_METRICS = ("ndcg@10", "recall@20", "mrr@10", "hit@1")


def abstention_confusion(rows: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Confusion matrix of "should abstain" (case label) vs "abstained" (no answer delivered); None without labels.

    Positive class = abstain. precision: of the abstentions, the share that should have abstained;
    recall: of the cases that should abstain, the share that did. An error row counts as an abstention
    (nothing was delivered) and is also counted in `errors`."""
    labelled = [row for row in rows if row.get("should_abstain") is not None]
    if not labelled:
        return None
    tp = sum(1 for r in labelled if r["should_abstain"] and not r.get("answered"))
    fn = sum(1 for r in labelled if r["should_abstain"] and r.get("answered"))
    fp = sum(1 for r in labelled if not r["should_abstain"] and not r.get("answered"))
    tn = sum(1 for r in labelled if not r["should_abstain"] and r.get("answered"))
    return {
        "labelled": len(labelled),
        "should_abstain_and_abstained": tp,
        "should_abstain_but_answered": fn,
        "should_answer_but_abstained": fp,
        "should_answer_and_answered": tn,
        "errors": sum(1 for r in labelled if is_error(r)),
        "precision": _rate(tp, tp + fp),
        "recall": _rate(tp, tp + fn),
    }


def retrieval_summary(
    rows: Sequence[Dict[str, Any]], *, metrics: Sequence[str] = RETRIEVAL_METRICS, resamples: int = 1000, seed: int = 0
) -> Optional[Dict[str, Any]]:
    """Mean and cluster-bootstrap 95% CI (clusters = case group) of retrieval metrics; None when no row has gold ids."""
    scored = [row for row in rows if row.get("retrieval")]
    if not scored:
        return None
    out: Dict[str, Any] = {"n": len(scored)}
    groups = [row.get("group") or row.get("case_id") for row in scored]
    for metric in metrics:
        values = [float(row["retrieval"].get(metric, 0.0)) for row in scored]
        out[metric] = bootstrap_ci(values, groups, resamples=resamples, seed=seed)
    return out


def summarize(rows: Sequence[Dict[str, Any]], *, alpha: float = 0.05) -> Dict[str, Any]:
    n = len(rows)
    passed = sum(1 for row in rows if row.get("passed"))
    answered = sum(1 for row in rows if row.get("answered"))
    false_accepts = sum(1 for row in rows if is_false_accept(row))
    latencies = [float(row["latency_ms"]) for row in rows if row.get("latency_ms") is not None]
    return {
        "n": n,
        "passed": passed,
        "pass_rate": _rate(passed, n),
        "answered": answered,
        "abstained": n - answered,
        "errors": sum(1 for row in rows if is_error(row)),
        "false_accepts": false_accepts,
        "false_accept_rate": _rate(false_accepts, n),
        "false_accept_upper": clopper_pearson_upper(false_accepts, n, alpha) if n else None,
        "latency_ms": latency_summary(latencies) if latencies else None,
        "latency_p95_ms": latency_summary(latencies)["p95_ms"] if latencies else None,
        "stage_latency_ms": _stage_latency(rows),
        "abstention": abstention_confusion(rows),
        "retrieval": retrieval_summary(rows),
        "citation_precision": _mean([r["citation_precision"] for r in rows if r.get("citation_precision") is not None]),
    }


def _mean(values: Sequence[float]) -> Optional[Dict[str, float]]:
    return {"mean": sum(values) / len(values), "n": float(len(values))} if values else None


def _stage_latency(rows: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Dict[str, float]]]:
    stages: Dict[str, List[float]] = {}
    for row in rows:
        for stage, value in (row.get("stage_ms") or {}).items():
            if value is not None:
                stages.setdefault(str(stage), []).append(float(value))
    return {stage: latency_summary(values) for stage, values in sorted(stages.items())} or None


def by_slice(rows: Sequence[Dict[str, Any]], *, alpha: float = 0.05) -> Dict[str, Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row.get("slice") or "(none)"), []).append(row)
    return {name: summarize(items, alpha=alpha) for name, items in sorted(groups.items())}
