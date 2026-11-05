"""Statistics for retrieval experiments.

Pure Python with fixed seeds, so every number in a report is reproducible.
- bootstrap_ci / paired_bootstrap_diff: cluster bootstrap (questions written
  from the same document are resampled together)
- mcnemar_exact: paired binary outcomes such as hit@10
- sign_flip_test: paired permutation test for mean differences
- holm: family-wise error control when many configurations are compared
- non_inferior: "not worse than baseline by more than margin"
- n_for_mcnemar / n_for_proportion_ci: how many questions are needed
- psi / ks_two_sample: drift between a reference and a current period
- pareto_front: configurations not dominated on quality, latency, memory
"""

from __future__ import annotations

import math
import random
from statistics import NormalDist
from typing import Any, Dict, List, Optional, Sequence

_NAN = float("nan")


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else _NAN


def percentile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile of already sorted values, q in [0, 1]."""
    if not sorted_values:
        return _NAN
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = q * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    frac = position - lower
    return float(sorted_values[lower] * (1 - frac) + sorted_values[upper] * frac)


def latency_summary(values_ms: Sequence[float]) -> Dict[str, float]:
    ordered = sorted(values_ms)
    return {
        "mean_ms": mean(ordered),
        "p50_ms": percentile(ordered, 0.50),
        "p95_ms": percentile(ordered, 0.95),
        "p99_ms": percentile(ordered, 0.99),
        "count": float(len(ordered)),
    }


def _clusters(groups: Optional[Sequence[Any]], n: int) -> List[List[int]]:
    if groups is None:
        return [[i] for i in range(n)]
    by_group: Dict[Any, List[int]] = {}
    for i, group in enumerate(groups):
        by_group.setdefault(group, []).append(i)
    return list(by_group.values())


def _bootstrap_means(values: Sequence[float], clusters: List[List[int]], resamples: int, seed: int) -> List[float]:
    rng = random.Random(seed)
    sums = [sum(values[i] for i in idx) for idx in clusters]
    sizes = [len(idx) for idx in clusters]
    k = len(clusters)
    out = []
    for _ in range(resamples):
        total, count = 0.0, 0
        for _ in range(k):
            j = rng.randrange(k)
            total += sums[j]
            count += sizes[j]
        out.append(total / count)
    out.sort()
    return out


def bootstrap_ci(
    values: Sequence[float],
    groups: Optional[Sequence[Any]] = None,
    *,
    resamples: int = 2000,
    alpha: float = 0.05,
    seed: int = 0,
) -> Dict[str, float]:
    if not values:
        return {"mean": _NAN, "low": _NAN, "high": _NAN, "n": 0.0, "clusters": 0.0}
    clusters = _clusters(groups, len(values))
    stats = _bootstrap_means(values, clusters, resamples, seed)
    return {
        "mean": mean(values),
        "low": percentile(stats, alpha / 2),
        "high": percentile(stats, 1 - alpha / 2),
        "n": float(len(values)),
        "clusters": float(len(clusters)),
    }


def paired_bootstrap_diff(
    baseline: Sequence[float],
    candidate: Sequence[float],
    groups: Optional[Sequence[Any]] = None,
    *,
    resamples: int = 2000,
    alpha: float = 0.05,
    seed: int = 0,
) -> Dict[str, float]:
    """CI of mean(candidate - baseline) over the same queries.

    p_value is the two-sided bootstrap p (twice the smaller tail mass at 0).
    """
    if len(baseline) != len(candidate):
        raise ValueError("paired samples must have the same length")
    diffs = [c - b for b, c in zip(baseline, candidate)]
    if not diffs:
        return {"diff": _NAN, "low": _NAN, "high": _NAN, "p_value": _NAN, "n": 0.0}
    stats = _bootstrap_means(diffs, _clusters(groups, len(diffs)), resamples, seed)
    below = sum(1 for s in stats if s <= 0) / len(stats)
    above = sum(1 for s in stats if s >= 0) / len(stats)
    return {
        "diff": mean(diffs),
        "low": percentile(stats, alpha / 2),
        "high": percentile(stats, 1 - alpha / 2),
        "p_value": min(1.0, 2 * min(below, above)),
        "n": float(len(diffs)),
    }


def mcnemar_exact(baseline_only: int, candidate_only: int) -> float:
    """Two-sided exact McNemar p from the discordant counts."""
    n = baseline_only + candidate_only
    if n == 0:
        return 1.0
    k = min(baseline_only, candidate_only)
    tail = sum(math.comb(n, i) for i in range(k + 1))
    return min(1.0, 2 * tail / 2**n)


def mcnemar_from_hits(baseline_hits: Sequence[float], candidate_hits: Sequence[float]) -> Dict[str, float]:
    if len(baseline_hits) != len(candidate_hits):
        raise ValueError("paired samples must have the same length")
    baseline_only = sum(1 for b, c in zip(baseline_hits, candidate_hits) if b > 0 and c <= 0)
    candidate_only = sum(1 for b, c in zip(baseline_hits, candidate_hits) if b <= 0 and c > 0)
    return {
        "baseline_only": float(baseline_only),
        "candidate_only": float(candidate_only),
        "p_value": mcnemar_exact(baseline_only, candidate_only),
    }


def sign_flip_test(diffs: Sequence[float], *, permutations: int = 10000, seed: int = 0) -> float:
    """Two-sided paired permutation p for mean(diffs) == 0 (with +1 smoothing)."""
    if not diffs:
        return _NAN
    rng = random.Random(seed)
    observed = abs(mean(diffs))
    extreme = 0
    for _ in range(permutations):
        flipped = sum(d if rng.random() < 0.5 else -d for d in diffs) / len(diffs)
        if abs(flipped) >= observed - 1e-12:
            extreme += 1
    return (extreme + 1) / (permutations + 1)


def holm(pvalues: Dict[str, float], alpha: float = 0.05) -> Dict[str, Dict[str, float]]:
    """Holm-Bonferroni adjusted p-values and rejection decisions."""
    ordered = sorted(pvalues.items(), key=lambda item: item[1])
    m = len(ordered)
    out: Dict[str, Dict[str, float]] = {}
    running = 0.0
    for i, (name, p) in enumerate(ordered):
        running = max(running, min(1.0, (m - i) * p))
        out[name] = {"p": p, "p_adjusted": running, "reject": float(running <= alpha)}
    return out


def non_inferior(diff_ci_low: float, margin: float) -> bool:
    """True when the lower CI bound of (candidate - baseline) is above -margin."""
    return diff_ci_low > -abs(margin)


def _z(q: float) -> float:
    return NormalDist().inv_cdf(q)


def n_for_mcnemar(delta: float, discordant: float, *, alpha: float = 0.05, power: float = 0.8) -> int:
    """Approximate questions needed to detect a paired difference delta in a
    binary metric when a fraction `discordant` of questions disagree."""
    if delta <= 0 or not 0 < discordant <= 1:
        raise ValueError("delta must be > 0 and discordant in (0, 1]")
    return math.ceil((_z(1 - alpha / 2) + _z(power)) ** 2 * discordant / delta**2)


def n_for_proportion_ci(p: float, half_width: float, *, alpha: float = 0.05) -> int:
    if half_width <= 0 or not 0 <= p <= 1:
        raise ValueError("half_width must be > 0 and p in [0, 1]")
    return math.ceil(_z(1 - alpha / 2) ** 2 * p * (1 - p) / half_width**2)


def proportion_ci_half_width(p: float, n: int, *, alpha: float = 0.05) -> float:
    return _z(1 - alpha / 2) * math.sqrt(p * (1 - p) / n) if n > 0 else _NAN


def psi(reference: Sequence[float], current: Sequence[float], *, bins: int = 10, eps: float = 1e-6) -> float:
    """Population stability index with quantile bins taken from the reference."""
    if not reference or not current:
        return _NAN
    ordered = sorted(reference)
    edges = [percentile(ordered, i / bins) for i in range(1, bins)]

    def _shares(values: Sequence[float]) -> List[float]:
        counts = [0] * bins
        for v in values:
            counts[sum(1 for e in edges if v > e)] += 1
        return [max(c / len(values), eps) for c in counts]

    ref, cur = _shares(reference), _shares(current)
    return sum((c - r) * math.log(c / r) for r, c in zip(ref, cur))


def ks_two_sample(a: Sequence[float], b: Sequence[float]) -> Dict[str, float]:
    """Two-sample Kolmogorov-Smirnov statistic with the asymptotic p-value."""
    if not a or not b:
        return {"d": _NAN, "p_value": _NAN}
    xs, ys = sorted(a), sorted(b)
    i = j = 0
    d = 0.0
    while i < len(xs) and j < len(ys):
        value = min(xs[i], ys[j])
        while i < len(xs) and xs[i] <= value:
            i += 1
        while j < len(ys) and ys[j] <= value:
            j += 1
        d = max(d, abs(i / len(xs) - j / len(ys)))
    en = math.sqrt(len(xs) * len(ys) / (len(xs) + len(ys)))
    lam = (en + 0.12 + 0.11 / en) * d
    p = 2 * sum((-1) ** (k - 1) * math.exp(-2 * k * k * lam * lam) for k in range(1, 101))
    return {"d": d, "p_value": min(1.0, max(0.0, p))}


def pareto_front(
    rows: Sequence[Dict[str, Any]], *, maximize: Sequence[str] = (), minimize: Sequence[str] = ()
) -> List[Dict[str, Any]]:
    """Rows not dominated by another row on every listed objective."""

    def _dominates(x: Dict[str, Any], y: Dict[str, Any]) -> bool:
        no_worse = all(x[k] >= y[k] for k in maximize) and all(x[k] <= y[k] for k in minimize)
        better = any(x[k] > y[k] for k in maximize) or any(x[k] < y[k] for k in minimize)
        return no_worse and better

    return [row for row in rows if not any(_dominates(other, row) for other in rows if other is not row)]
