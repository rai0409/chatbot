"""Summaries of evaluation rows (see runner.run_cases for the row fields)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from eval.bench.stats import latency_summary
from eval.tenant_eval.stats import clopper_pearson_upper


def _rate(count: int, n: int) -> Optional[float]:
    return count / n if n else None


def is_error(row: Dict[str, Any]) -> bool:
    return "error" in row.get("failures", [])


def is_false_accept(row: Dict[str, Any]) -> bool:
    """The bot answered, and the answer fails the case. An abstention or an error is not a false accept."""
    return bool(row.get("answered")) and not row.get("passed") and not is_error(row)


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
    }


def by_slice(rows: Sequence[Dict[str, Any]], *, alpha: float = 0.05) -> Dict[str, Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row.get("slice") or "(none)"), []).append(row)
    return {name: summarize(items, alpha=alpha) for name, items in sorted(groups.items())}
