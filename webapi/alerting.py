from __future__ import annotations

# Minimal, local-only alert evaluation over the /metrics JSON snapshot.
#
# Pure functions: no network, no Docker, no external Prometheus, no secrets.
# Input is the same payload shape /metrics returns (uptime_seconds,
# total_requests, error_requests, counters{name: {label: count}}). Output is a
# per-signal OK/WARN/CRITICAL verdict. Thresholds mirror the documented
# starting points in docs/operations.md ("Alert thresholds"); operators tune
# them per deployment. Counters are per-process (same caveat as /metrics).
#
# This module reads only metric names (stable enum-like labels) and integer
# counts. It never touches prompts, document text, API keys, tenant secrets,
# or .env-derived values.

from typing import Any, Dict, List, Optional


OK = "OK"
WARN = "WARN"
CRITICAL = "CRITICAL"

_SEVERITY_ORDER = {OK: 0, WARN: 1, CRITICAL: 2}

# Defaults align with docs/operations.md. Rates are fractions (0..1).
DEFAULT_THRESHOLDS: Dict[str, Any] = {
    # provider/chat error rate
    "error_rate": {"warn": 0.02, "critical": 0.10},
    # abstain / no-answer (extractive/no-answer fallback) rate
    "fallback_rate": {"warn": 0.30, "critical": 0.60},
    # confidence-guard trip rate
    "guard_trip_rate": {"warn": 0.40, "critical": 0.80},
    # absolute 429 count over the window
    "rate_limited": {"warn": 1, "critical": 25},
    # absolute auth-rejection count over the window
    "auth_rejection": {"warn": 5, "critical": 50},
    # feedback human-review-request rate (only if feedback metrics present)
    "human_review_rate": {"warn": 0.20, "critical": 0.50},
    # optional p95 latency in ms (only evaluated if provided in the snapshot)
    "latency_p95_ms": {"warn": 4000, "critical": 10000},
    # below this many chat requests, rate-based signals report OK with a note
    # (a tiny window is statistically meaningless).
    "min_requests_for_rate": 20,
}

# Answer modes that count as a successful (served) answer.
_SUCCESS_MODES = {"grounded", "approved_exact_match", "approved_alias_match"}


def _counter(payload: Dict[str, Any], name: str) -> Dict[str, int]:
    counters = payload.get("counters") if isinstance(payload, dict) else None
    if not isinstance(counters, dict):
        return {}
    bucket = counters.get(name)
    return bucket if isinstance(bucket, dict) else {}


def _sum(bucket: Dict[str, int]) -> int:
    return int(sum(int(v) for v in bucket.values())) if bucket else 0


def chat_request_count(payload: Dict[str, Any]) -> int:
    # Chat-specific denominator: number of completed chat answers across modes.
    return _sum(_counter(payload, "chat_answer_mode_total"))


def chat_success_count(payload: Dict[str, Any]) -> int:
    modes = _counter(payload, "chat_answer_mode_total")
    return int(sum(int(c) for m, c in modes.items() if m in _SUCCESS_MODES))


def _rate_status(rate: float, warn: float, critical: float) -> str:
    if rate >= critical:
        return CRITICAL
    if rate >= warn:
        return WARN
    return OK


def _count_status(count: int, warn: int, critical: int) -> str:
    if count >= critical:
        return CRITICAL
    if count >= warn:
        return WARN
    return OK


def _worst(statuses: List[str]) -> str:
    worst = OK
    for s in statuses:
        if _SEVERITY_ORDER.get(s, 0) > _SEVERITY_ORDER[worst]:
            worst = s
    return worst


def evaluate_alerts(
    payload: Dict[str, Any], thresholds: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    t = dict(DEFAULT_THRESHOLDS)
    if thresholds:
        t.update(thresholds)

    requests = chat_request_count(payload)
    successes = chat_success_count(payload)
    errors = _sum(_counter(payload, "chat_provider_error_total"))
    fallbacks = _sum(_counter(payload, "chat_used_fallback_total"))
    guard_trips = _sum(_counter(payload, "chat_guard_reason_total"))
    rate_limited = _sum(_counter(payload, "api_rate_limited_total"))
    auth_rejections = _sum(_counter(payload, "api_auth_rejection_total"))
    feedback = _counter(payload, "chat_feedback_total")
    feedback_total = _sum(feedback)
    human_review = int(feedback.get("human_review_requested", 0)) if feedback else 0

    min_req = int(t.get("min_requests_for_rate", 0) or 0)
    enough = requests >= min_req
    signals: List[Dict[str, Any]] = []

    def rate_signal(name: str, numerator: int, key: str, denom: int) -> None:
        cfg = t[key]
        if denom <= 0 or not enough:
            signals.append({
                "name": name, "status": OK, "value": None,
                "warn": cfg["warn"], "critical": cfg["critical"],
                "detail": "insufficient_volume" if denom > 0 else "no_requests",
            })
            return
        rate = numerator / denom
        signals.append({
            "name": name, "status": _rate_status(rate, cfg["warn"], cfg["critical"]),
            "value": round(rate, 4), "warn": cfg["warn"], "critical": cfg["critical"],
            "detail": f"{numerator}/{denom}",
        })

    rate_signal("error_rate", errors, "error_rate", requests)
    rate_signal("fallback_rate", fallbacks, "fallback_rate", requests)
    rate_signal("guard_trip_rate", guard_trips, "guard_trip_rate", requests)

    # Zero successful answers over a non-empty chat window is CRITICAL.
    window_nonempty = (requests + errors) > 0
    zero_success = window_nonempty and successes == 0
    signals.append({
        "name": "zero_success",
        "status": CRITICAL if zero_success else OK,
        "value": successes,
        "detail": f"successes={successes} requests={requests} errors={errors}",
    })

    # Absolute-count signals (meaningful even at low volume).
    rl = t["rate_limited"]
    signals.append({
        "name": "rate_limited", "status": _count_status(rate_limited, rl["warn"], rl["critical"]),
        "value": rate_limited, "warn": rl["warn"], "critical": rl["critical"],
    })
    ar = t["auth_rejection"]
    signals.append({
        "name": "auth_rejection", "status": _count_status(auth_rejections, ar["warn"], ar["critical"]),
        "value": auth_rejections, "warn": ar["warn"], "critical": ar["critical"],
    })

    # Feedback human-review spike — only when feedback metrics are present.
    if feedback_total > 0:
        cfg = t["human_review_rate"]
        rate = human_review / feedback_total
        signals.append({
            "name": "human_review_rate",
            "status": _rate_status(rate, cfg["warn"], cfg["critical"]),
            "value": round(rate, 4), "warn": cfg["warn"], "critical": cfg["critical"],
            "detail": f"{human_review}/{feedback_total}",
        })

    # Optional latency p95 — only when explicitly provided in the snapshot.
    p95 = payload.get("latency_p95_ms") if isinstance(payload, dict) else None
    if isinstance(p95, (int, float)):
        cfg = t["latency_p95_ms"]
        signals.append({
            "name": "latency_p95_ms",
            "status": _count_status(int(p95), cfg["warn"], cfg["critical"]),
            "value": int(p95), "warn": cfg["warn"], "critical": cfg["critical"],
        })

    overall = _worst([s["status"] for s in signals])
    return {
        "overall": overall,
        "chat_requests": requests,
        "chat_successes": successes,
        "signals": signals,
    }


def status_exit_code(overall: str) -> int:
    # Operator-friendly: 0 OK, 1 WARN, 2 CRITICAL.
    return {OK: 0, WARN: 1, CRITICAL: 2}.get(overall, 2)
