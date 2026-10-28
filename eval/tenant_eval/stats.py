"""Binomial bounds and multiple-comparison helpers that eval.bench.stats does not have."""
from __future__ import annotations

import math
from typing import Dict


def _cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p), summed in log space (n in the thousands does not overflow)."""
    if k < 0:
        return 0.0
    if k >= n or p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 0.0
    total = 0.0
    for i in range(k + 1):
        log_pmf = (
            math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * math.log(p) + (n - i) * math.log1p(-p)
        )
        total += math.exp(log_pmf)
    return min(1.0, total)


def _check(k: int, n: int, alpha: float) -> None:
    if not (0 < alpha < 1) or n < 0 or not 0 <= k <= max(n, 0):
        raise ValueError("need 0 <= k <= n and 0 < alpha < 1")


def clopper_pearson_upper(k: int, n: int, alpha: float = 0.05) -> float:
    """One-sided upper confidence bound (level 1 - alpha) of a proportion with k events in n trials."""
    _check(k, n, alpha)
    if n == 0 or k == n:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if _cdf(k, n, mid) > alpha:
            lo = mid
        else:
            hi = mid
    return hi


def clopper_pearson_lower(k: int, n: int, alpha: float = 0.05) -> float:
    """One-sided lower confidence bound (level 1 - alpha) of a proportion with k events in n trials."""
    _check(k, n, alpha)
    if n == 0 or k == 0:
        return 0.0
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if 1.0 - _cdf(k - 1, n, mid) > alpha:
            hi = mid
        else:
            lo = mid
    return lo


def rule_of_three_upper(n: int) -> float:
    """Approximate 95 % upper bound of a rate when 0 events were seen in n trials."""
    return 1.0 if n <= 0 else min(1.0, 3.0 / n)


def min_cases_for_zero_failures(limit: float, alpha: float = 0.05) -> int:
    """Fewest cases with no failure at all that show the failure rate is below `limit` (one-sided, level 1 - alpha)."""
    if not (0 < limit < 1) or not (0 < alpha < 1):
        raise ValueError("limit and alpha must be in (0, 1)")
    return math.ceil(math.log(alpha) / math.log1p(-limit))


def benjamini_hochberg(pvalues: Dict[str, float], q: float = 0.05) -> Dict[str, Dict[str, float]]:
    """BH-adjusted p-values and rejection decisions (false discovery rate q)."""
    ordered = sorted(pvalues.items(), key=lambda item: item[1])
    m = len(ordered)
    adjusted: Dict[str, float] = {}
    running = 1.0
    for rank in range(m, 0, -1):
        name, p = ordered[rank - 1]
        running = min(running, min(1.0, p * m / rank))
        adjusted[name] = running
    return {name: {"p": p, "p_adjusted": adjusted[name], "reject": float(adjusted[name] <= q)} for name, p in ordered}
