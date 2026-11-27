"""Decide whether a candidate may replace the baseline, from paired evaluation rows.

decision: "fail" if any check fails, else "insufficient" if any check cannot be settled with this many cases,
else "pass". The numbers of Policy are initial values; tune them on real data.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Sequence

from eval.bench.stats import holm, mcnemar_exact, non_inferior, paired_bootstrap_diff
from eval.tenant_eval.metrics import is_error, summarize


@dataclass(frozen=True)
class Policy:
    margin: float = 0.02  # the pass rate may not fall by more than this (non-inferiority margin)
    min_cases: int = 30  # fewer cases than this cannot settle the overall comparison
    min_slice: int = 30  # a slice with fewer cases is reported but not judged
    false_accept_limit: float = 0.01  # upper bound the candidate's false-accept rate must stay under
    alpha: float = 0.05  # level of the bounds and of the Holm-corrected slice tests
    ci_alpha: float = 0.10  # two-sided level of the paired CI (its lower end is a one-sided 95 % bound)
    protected_priority: int = 100  # cases at or above this priority that pass in the baseline must still pass
    resamples: int = 2000
    seed: int = 0
    latency_p95_budget_ms: Optional[float] = None  # candidate end-to-end p95 must stay under this (None: not checked)
    retrieval_margin: float = 0.02  # nDCG@10 may not fall by more than this (only when cases carry gold ids)


def _check(name: str, status: str, **detail: Any) -> Dict[str, Any]:
    return {"name": name, "status": status, **detail}


def compare(
    baseline_rows: Sequence[Dict[str, Any]], candidate_rows: Sequence[Dict[str, Any]], policy: Policy = Policy()
) -> Dict[str, Any]:
    base = {row["case_id"]: row for row in baseline_rows}
    cand = {row["case_id"]: row for row in candidate_rows}
    if len(base) != len(baseline_rows) or len(cand) != len(candidate_rows) or set(base) != set(cand):
        raise ValueError("baseline and candidate must cover the same cases, each once")
    ids = sorted(base)
    b_rows, c_rows = [base[i] for i in ids], [cand[i] for i in ids]
    b_hits = [1.0 if row.get("passed") else 0.0 for row in b_rows]
    c_hits = [1.0 if row.get("passed") else 0.0 for row in c_rows]
    n = len(ids)
    checks: List[Dict[str, Any]] = []

    # 1. the pass rate is not worse by more than the margin
    if n < policy.min_cases:
        checks.append(_check("overall_non_inferiority", "insufficient", n=n, needed=policy.min_cases))
    else:
        diff = paired_bootstrap_diff(
            b_hits, c_hits, [row.get("group") or row["case_id"] for row in b_rows],
            resamples=policy.resamples, alpha=policy.ci_alpha, seed=policy.seed,
        )
        ok = non_inferior(diff["low"], policy.margin)
        checks.append(
            _check("overall_non_inferiority", "pass" if ok else "fail", n=n, diff=diff["diff"], low=diff["low"],
                   high=diff["high"], margin=policy.margin)
        )

    # 2. no slice (question type) got significantly worse
    slices: Dict[str, List[int]] = {}
    for index, row in enumerate(b_rows):
        slices.setdefault(str(row.get("slice") or "(none)"), []).append(index)
    pvalues: Dict[str, float] = {}
    not_judged: Dict[str, int] = {}
    for name, members in sorted(slices.items()):
        if len(members) < policy.min_slice:
            not_judged[name] = len(members)
            continue
        worse = sum(1 for i in members if b_hits[i] > 0 and c_hits[i] <= 0)
        better = sum(1 for i in members if b_hits[i] <= 0 and c_hits[i] > 0)
        pvalues[name] = mcnemar_exact(worse, better) if worse > better else 1.0
    if not pvalues:
        checks.append(_check("slice_regression", "not_applicable", not_judged=not_judged))
    else:
        adjusted = holm(pvalues, policy.alpha)
        regressed = sorted(name for name, result in adjusted.items() if result["reject"])
        checks.append(
            _check("slice_regression", "fail" if regressed else "pass", regressed=regressed, judged=sorted(pvalues),
                   not_judged=not_judged, p_adjusted={name: r["p_adjusted"] for name, r in adjusted.items()})
        )

    # 3. protected cases never regress
    lost = [
        row["case_id"] for row, b, c in zip(b_rows, b_hits, c_hits)
        if b > 0 and c <= 0 and int(row.get("priority") or 0) >= policy.protected_priority
    ]
    checks.append(_check("protected_cases", "fail" if lost else "pass", regressed=lost))

    # 4. the false-accept rate: under the limit is proven, a worse count fails, anything else is unproven
    b_sum, c_sum = summarize(b_rows, alpha=policy.alpha), summarize(c_rows, alpha=policy.alpha)
    upper = c_sum["false_accept_upper"]
    if upper is not None and upper <= policy.false_accept_limit:
        status = "pass"
    elif c_sum["false_accepts"] > b_sum["false_accepts"]:
        status = "fail"
    else:
        status = "insufficient"
    checks.append(
        _check("false_accept", status, baseline=b_sum["false_accepts"], candidate=c_sum["false_accepts"], n=n,
               upper=upper, limit=policy.false_accept_limit)
    )

    # 5. no new errors
    new_errors = sorted(i for i, b, c in zip(ids, b_rows, c_rows) if is_error(c) and not is_error(b))
    checks.append(_check("errors", "fail" if new_errors else "pass", new=new_errors))

    # 6. latency SLO: the candidate's end-to-end p95 within the budget
    if policy.latency_p95_budget_ms is None:
        checks.append(_check("latency_p95", "not_applicable"))
    elif c_sum["latency_p95_ms"] is None:
        checks.append(_check("latency_p95", "insufficient", budget_ms=policy.latency_p95_budget_ms))
    else:
        ok = c_sum["latency_p95_ms"] <= policy.latency_p95_budget_ms
        checks.append(_check("latency_p95", "pass" if ok else "fail", baseline_ms=b_sum["latency_p95_ms"],
                             candidate_ms=c_sum["latency_p95_ms"], budget_ms=policy.latency_p95_budget_ms))

    # 7. retrieval: nDCG@10 of cases with gold ids is non-inferior (paired, cluster bootstrap)
    with_gold = [i for i, (b, c) in enumerate(zip(b_rows, c_rows)) if b.get("retrieval") and c.get("retrieval")]
    if not with_gold:
        checks.append(_check("retrieval_ndcg", "not_applicable"))
    elif len(with_gold) < policy.min_cases:
        checks.append(_check("retrieval_ndcg", "insufficient", n=len(with_gold), needed=policy.min_cases))
    else:
        r_diff = paired_bootstrap_diff(
            [float(b_rows[i]["retrieval"].get("ndcg@10", 0.0)) for i in with_gold],
            [float(c_rows[i]["retrieval"].get("ndcg@10", 0.0)) for i in with_gold],
            [b_rows[i].get("group") or b_rows[i]["case_id"] for i in with_gold],
            resamples=policy.resamples, alpha=policy.ci_alpha, seed=policy.seed,
        )
        ok = non_inferior(r_diff["low"], policy.retrieval_margin)
        checks.append(_check("retrieval_ndcg", "pass" if ok else "fail", n=len(with_gold), diff=r_diff["diff"],
                             low=r_diff["low"], high=r_diff["high"], margin=policy.retrieval_margin))

    statuses = {check["status"] for check in checks}
    decision = "fail" if "fail" in statuses else "insufficient" if "insufficient" in statuses else "pass"
    return {
        "decision": decision,
        "checks": checks,
        "policy": asdict(policy),
        "baseline": b_sum,
        "candidate": c_sum,
        "changed": {
            "worse": [i for i, b, c in zip(ids, b_hits, c_hits) if b > 0 and c <= 0],
            "better": [i for i, b, c in zip(ids, b_hits, c_hits) if b <= 0 and c > 0],
        },
    }
