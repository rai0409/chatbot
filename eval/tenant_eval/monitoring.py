"""Drift of the live traffic against a reference period, from chat audit events."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from statistics import NormalDist
from typing import Any, Dict, List, Mapping, Sequence

from eval.bench.stats import ks_two_sample, psi

_LEVEL = {"ok": 0, "warn": 1, "alert": 2}


@dataclass(frozen=True)
class DriftPolicy:
    min_samples: int = 200  # per period; fewer cannot settle anything
    psi_warn: float = 0.1  # the usual reading: < 0.1 stable, 0.1-0.25 shifted, > 0.25 clearly changed
    psi_alert: float = 0.25
    ks_alpha: float = 0.01
    ks_min_d: float = 0.1  # a KS test on thousands of events finds trivial shifts; ask for a visible one too
    rate_min_delta: float = 0.05  # a rate must move by this much (and be significant) to warn; twice this to alert
    rate_alpha: float = 0.01


def extract(events: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """The signals of chat audit events. Cache hits are left out of the numeric ones (their latency is not the pipeline's)."""
    live = [event for event in events if not event.get("cache_hit")]
    return {
        "n": len(events),
        "n_live": len(live),
        "guard": sum(1 for event in events if event.get("guard_reason")),
        "fallback": sum(1 for event in events if event.get("used_fallback")),
        "citations": [float(event["citations_count"]) for event in live if isinstance(event.get("citations_count"), (int, float))],
        "latency_ms": [float(event["latency_ms"]) for event in live if isinstance(event.get("latency_ms"), (int, float))],
        "intents": [str(event.get("intent") or "other") for event in events],
    }


def two_proportion_p(k1: int, n1: int, k2: int, n2: int) -> float:
    """Two-sided p-value of equal rates (pooled normal approximation; fine for the sample sizes asked for)."""
    if n1 <= 0 or n2 <= 0:
        return 1.0
    pooled = (k1 + k2) / (n1 + n2)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if se == 0:
        return 1.0 if k1 / n1 == k2 / n2 else 0.0
    z = abs(k1 / n1 - k2 / n2) / se
    return min(1.0, 2 * (1 - NormalDist().cdf(z)))


def categorical_psi(reference: Sequence[str], current: Sequence[str], eps: float = 1e-6) -> float:
    if not reference or not current:
        return float("nan")
    values = sorted(set(reference) | set(current))
    total = 0.0
    for value in values:
        r = max(reference.count(value) / len(reference), eps)
        c = max(current.count(value) / len(current), eps)
        total += (c - r) * math.log(c / r)
    return total


def _worst(*levels: str) -> str:
    return max(levels, key=lambda level: _LEVEL[level])


def _rate_signal(name: str, k_ref: int, n_ref: int, k_cur: int, n_cur: int, policy: DriftPolicy) -> Dict[str, Any]:
    delta = k_cur / n_cur - k_ref / n_ref
    p = two_proportion_p(k_ref, n_ref, k_cur, n_cur)
    level = "ok"
    if abs(delta) >= policy.rate_min_delta and p < policy.rate_alpha:
        level = "alert" if abs(delta) >= 2 * policy.rate_min_delta else "warn"
    return {"signal": name, "status": level, "reference": k_ref / n_ref, "current": k_cur / n_cur, "delta": delta, "p_value": p}


def _numeric_signal(name: str, ref: List[float], cur: List[float], policy: DriftPolicy) -> Dict[str, Any]:
    if len(ref) < policy.min_samples or len(cur) < policy.min_samples:
        return {"signal": name, "status": "insufficient", "n_reference": len(ref), "n_current": len(cur)}
    value = psi(ref, cur)
    ks = ks_two_sample(ref, cur)
    psi_level = "alert" if value >= policy.psi_alert else "warn" if value >= policy.psi_warn else "ok"
    ks_level = "warn" if ks["p_value"] < policy.ks_alpha and ks["d"] >= policy.ks_min_d else "ok"
    return {"signal": name, "status": _worst(psi_level, ks_level), "psi": value, "ks_d": ks["d"], "ks_p": ks["p_value"]}


def compare(
    reference_events: Sequence[Mapping[str, Any]], current_events: Sequence[Mapping[str, Any]],
    policy: DriftPolicy = DriftPolicy(),
) -> Dict[str, Any]:
    """Compare a current period with a reference period. status: ok / warn / alert / insufficient."""
    ref, cur = extract(reference_events), extract(current_events)
    result: Dict[str, Any] = {"policy": asdict(policy), "n_reference": ref["n"], "n_current": cur["n"], "signals": []}
    if ref["n"] < policy.min_samples or cur["n"] < policy.min_samples:
        result["status"] = "insufficient"
        return result
    signals = [
        _rate_signal("guard_rate", ref["guard"], ref["n"], cur["guard"], cur["n"], policy),
        _rate_signal("fallback_rate", ref["fallback"], ref["n"], cur["fallback"], cur["n"], policy),
        _numeric_signal("citations_count", ref["citations"], cur["citations"], policy),
        _numeric_signal("latency_ms", ref["latency_ms"], cur["latency_ms"], policy),
    ]
    mix = categorical_psi(ref["intents"], cur["intents"])
    signals.append({
        "signal": "intent_mix",
        "status": "alert" if mix >= policy.psi_alert else "warn" if mix >= policy.psi_warn else "ok",
        "psi": mix,
    })
    result["signals"] = signals
    judged = [s["status"] for s in signals if s["status"] in _LEVEL]
    result["status"] = _worst("ok", *judged)
    return result
