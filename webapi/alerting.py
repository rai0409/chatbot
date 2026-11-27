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
    # LLM gateway (rag_core/llm_gateway.py): share of calls that reached an endpoint and failed; calls skipped because a breaker
    # is open (the answers are extractive meanwhile); calls refused by a daily limit. Evaluated only when the counters exist.
    "llm_error_rate": {"warn": 0.10, "critical": 0.50},
    "llm_min_calls": 10,
    "llm_breaker_open": {"warn": 1, "critical": 100},
    "llm_quota_exceeded": {"warn": 1, "critical": 100},
    # 2026-11 ops hardening: counts over the window (deltas between two snapshots, see window_payload)
    "ingestion_job_failures": {"warn": 1, "critical": 5},
    "operation_failures": {"warn": 1, "critical": 3},
    # state carried in payload["ops"] (webapi/routers/infra.ops_state)
    "disk_used_fraction": {"warn": 0.85, "critical": 0.95},
}

# Answer modes that count as a successful (served) answer.
_SUCCESS_MODES = {"grounded", "approved_exact_match", "approved_alias_match", "approved_colloquial_match"}


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

    # Audit records that could not be written (disk full, permissions): any is
    # CRITICAL. The counter is in the snapshot only when it is above zero.
    audit_failures = _sum(_counter(payload, "audit_write_failures_total"))
    if audit_failures > 0:
        signals.append({
            "name": "audit_write_failures", "status": CRITICAL, "value": audit_failures,
            "detail": f"audit_write_failures={audit_failures}",
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

    # The LLM: failures, a stopped endpoint (a breaker is open: the answers are extractive), a daily limit that was reached.
    llm_calls = _counter(payload, "llm_calls_total")
    if llm_calls:
        reached = sum(v for k, v in llm_calls.items() if k.endswith(":ok") or k.endswith(":error"))
        failed = sum(v for k, v in llm_calls.items() if k.endswith(":error"))
        cfg = t["llm_error_rate"]
        if reached >= int(t.get("llm_min_calls", 0) or 0):
            rate = failed / reached
            signals.append({"name": "llm_error_rate", "status": _rate_status(rate, cfg["warn"], cfg["critical"]),
                            "value": round(rate, 4), "warn": cfg["warn"], "critical": cfg["critical"], "detail": f"{failed}/{reached}"})
    for counter, key in (("llm_breaker_open_total", "llm_breaker_open"), ("llm_quota_exceeded_total", "llm_quota_exceeded")):
        count = _sum(_counter(payload, counter))
        if count > 0:
            cfg = t[key]
            signals.append({"name": key, "status": _count_status(count, cfg["warn"], cfg["critical"]), "value": count,
                            "warn": cfg["warn"], "critical": cfg["critical"]})

    # Ingestion jobs that failed or were interrupted, promotions/deletions that failed or were interrupted,
    # in this window (counters of webapi/ingestion_jobs).
    jobs = _counter(payload, "ingestion_job_total")
    job_failures = int(jobs.get("error", 0)) + int(jobs.get("interrupted", 0)) if jobs else 0
    if job_failures > 0:
        cfg = t["ingestion_job_failures"]
        signals.append({"name": "ingestion_job_failures", "status": _count_status(job_failures, cfg["warn"], cfg["critical"]),
                        "value": job_failures, "warn": cfg["warn"], "critical": cfg["critical"]})
    operations = _sum(_counter(payload, "ops_operation_total"))
    if operations > 0:
        cfg = t["operation_failures"]
        signals.append({"name": "operation_failures", "status": _count_status(operations, cfg["warn"], cfg["critical"]),
                        "value": operations, "warn": cfg["warn"], "critical": cfg["critical"],
                        "detail": ",".join(sorted(_counter(payload, "ops_operation_total")))})
    for counter in ("source_ledger_chain_broken_total", "source_ledger_torn_total"):
        count = _sum(_counter(payload, counter))
        if count > 0:
            signals.append({"name": counter.replace("_total", ""),
                            "status": CRITICAL if "broken" in counter else WARN, "value": count})
    ops = payload.get("ops") if isinstance(payload, dict) else None
    if isinstance(ops, dict):
        signals.extend(_ops_signals(ops, t))

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


def window_payload(current: Dict[str, Any], previous: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The snapshot of one monitoring window: counters minus the previous snapshot's.

    Counters are cumulative since the process started, so evaluating them directly alerts forever after one bad
    hour. A counter that went down (the API restarted) is taken as it is. The operational state ("ops") and the
    latency are current values and are kept. Without a previous snapshot the current one is returned."""
    if not isinstance(current, dict):
        return {}
    if not isinstance(previous, dict):
        return dict(current)
    out = dict(current)
    prev_counters = previous.get("counters") if isinstance(previous.get("counters"), dict) else {}
    counters: Dict[str, Dict[str, int]] = {}
    for name, bucket in (current.get("counters") or {}).items():
        if not isinstance(bucket, dict):
            continue
        before = prev_counters.get(name) if isinstance(prev_counters.get(name), dict) else {}
        try:
            restarted = any(int(before.get(k, 0)) > int(v) for k, v in bucket.items())
            delta = {k: int(v) - (0 if restarted else int(before.get(k, 0))) for k, v in bucket.items()}
        except (TypeError, ValueError):  # a hand-edited or damaged snapshot: take the current counters as they are
            delta = {k: int(v) for k, v in bucket.items() if isinstance(v, (int, float))}
        delta = {k: v for k, v in delta.items() if v > 0}
        if delta:
            counters[name] = delta
    out["counters"] = counters
    for key in ("total_requests", "error_requests"):
        try:
            now_v, prev_v = int(current.get(key) or 0), int(previous.get(key) or 0)
            out[key] = now_v - prev_v if now_v >= prev_v else now_v
        except (TypeError, ValueError):
            pass
    out["window"] = True
    return out


def _ops_signals(ops: Dict[str, Any], t: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Signals from the operational state; a part that could not be read is a WARN (monitoring blind spot)."""
    signals: List[Dict[str, Any]] = []

    def add(name: str, status: str, value: Any, detail: str = "") -> None:
        signals.append({"name": name, "status": status, "value": value, "detail": detail})

    for part, values in sorted(ops.items()):
        if isinstance(values, dict) and "error" in values:
            add(f"ops_{part}_unavailable", WARN, None, str(values["error"]))
    registry = ops.get("registry") if isinstance(ops.get("registry"), dict) else {}
    if int(registry.get("collections_stuck_writing") or 0) > 0:
        add("collections_stuck_writing", WARN, registry["collections_stuck_writing"],
            "a collection stays 'writing': an ingestion job died; re-run it")
    if int(registry.get("operations_interrupted") or 0) > 0:
        add("operations_interrupted", CRITICAL, registry["operations_interrupted"],
            "a promotion/deletion was interrupted: re-run it (idempotent)")
    if int(registry.get("operations_failed") or 0) > 0:
        add("operations_failed", WARN, registry["operations_failed"])
    sources = ops.get("sources") if isinstance(ops.get("sources"), dict) else {}
    if sources.get("ledger_chain_ok") is False:
        add("source_ledger_chain", CRITICAL, False, "the source change ledger chain is broken: every apply fails")
    if int(sources.get("apply_failed") or 0) > 0:
        add("source_apply_failed", WARN, sources["apply_failed"])
    if int(sources.get("held") or 0) > 0:
        add("source_apply_held", WARN, sources["held"], "a source plan waits for approval")
    index = ops.get("index") if isinstance(ops.get("index"), dict) else {}
    if index.get("pointer_ok") is False:
        add("generation_pointer", CRITICAL, False, "the active generation file is unreadable")
    if index.get("fingerprint") == "mismatch":
        add("embedding_fingerprint", CRITICAL, "mismatch", "the running embedding profile differs from the served manifest")
    if index.get("manifest_status") in ("stale", "unverified"):
        add("served_manifest", WARN, index["manifest_status"], "verify the served generation (switch_generation.py verify)")
    if index.get("count_drift"):
        add("index_count_drift", WARN, True, "live vector/keyword counts differ from the verified manifest")
    backup = ops.get("backup") if isinstance(ops.get("backup"), dict) else {}
    if backup.get("failed"):
        add("backup_failed", CRITICAL, True, "the last backup failed")
    elif backup and backup.get("too_old"):
        add("backup_age", WARN, backup.get("newest_age_hours"), f"older than {backup.get('max_age_hours')} h")
    disk = ops.get("disk") if isinstance(ops.get("disk"), dict) else {}
    if isinstance(disk.get("used_fraction"), (int, float)):
        cfg = t["disk_used_fraction"]
        used = float(disk["used_fraction"])
        status = _rate_status(used, cfg["warn"], cfg["critical"])
        if status != OK:
            add("disk_usage", status, used)
    return signals


def status_exit_code(overall: str) -> int:
    # Operator-friendly: 0 OK, 1 WARN, 2 CRITICAL.
    return {OK: 0, WARN: 1, CRITICAL: 2}.get(overall, 2)
