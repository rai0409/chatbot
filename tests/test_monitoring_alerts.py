from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

import config
from rag_core import answer_cache
from webapi import main, metrics_registry
from webapi.alerting import (
    CRITICAL,
    OK,
    WARN,
    evaluate_alerts,
    status_exit_code,
)

ROOT = Path(__file__).resolve().parents[1]


def _snapshot(answer_modes=None, **counters):
    c = {}
    if answer_modes is not None:
        c["chat_answer_mode_total"] = answer_modes
    c.update(counters)
    return {"uptime_seconds": 1, "total_requests": 1, "error_requests": 0, "counters": c}


# --- 1. normal inputs -> OK -------------------------------------------------


def test_healthy_window_is_ok():
    res = evaluate_alerts(_snapshot(
        answer_modes={"grounded": 90, "approved_exact_match": 5, "fallback": 5},
        chat_used_fallback_total={"total": 5},
        chat_provider_error_total={},
        chat_guard_reason_total={"too_general": 5},
    ))
    assert res["overall"] == OK
    assert {s["name"] for s in res["signals"]} >= {
        "error_rate", "fallback_rate", "guard_trip_rate", "zero_success",
        "rate_limited", "auth_rejection",
    }


def test_status_exit_codes():
    assert status_exit_code(OK) == 0
    assert status_exit_code(WARN) == 1
    assert status_exit_code(CRITICAL) == 2


def test_small_window_does_not_alert_on_rates():
    # Below min_requests_for_rate, rate signals report OK with insufficient note.
    res = evaluate_alerts(_snapshot(
        answer_modes={"grounded": 1, "fallback": 1},
        chat_provider_error_total={"timeout": 1},
    ))
    err = next(s for s in res["signals"] if s["name"] == "error_rate")
    assert err["status"] == OK
    assert err["detail"] in {"insufficient_volume", "no_requests"}


# --- 2. high error rate detected -------------------------------------------


def test_high_error_rate_is_critical():
    res = evaluate_alerts(_snapshot(
        answer_modes={"grounded": 50, "fallback": 50},
        chat_provider_error_total={"rate_limited": 30},
    ))
    err = next(s for s in res["signals"] if s["name"] == "error_rate")
    assert err["status"] == CRITICAL
    assert res["overall"] == CRITICAL


# --- 3. high abstain/no-answer rate detected -------------------------------


def test_high_fallback_rate_is_detected():
    res = evaluate_alerts(_snapshot(
        answer_modes={"grounded": 50, "fallback": 50},
        chat_used_fallback_total={"total": 50},
    ))
    fb = next(s for s in res["signals"] if s["name"] == "fallback_rate")
    assert fb["status"] in {WARN, CRITICAL}
    assert fb["value"] == 0.5


def test_high_guard_trip_rate_is_detected():
    res = evaluate_alerts(_snapshot(
        answer_modes={"grounded": 50, "fallback": 50},
        chat_guard_reason_total={"too_general": 50},
    ))
    g = next(s for s in res["signals"] if s["name"] == "guard_trip_rate")
    assert g["status"] in {WARN, CRITICAL}


# --- 4. zero success on non-empty window -----------------------------------


def test_zero_success_nonempty_window_is_critical():
    res = evaluate_alerts(_snapshot(answer_modes={"fallback": 30}))
    zs = next(s for s in res["signals"] if s["name"] == "zero_success")
    assert zs["status"] == CRITICAL
    assert res["overall"] == CRITICAL


def test_empty_window_is_not_zero_success_alert():
    res = evaluate_alerts(_snapshot(answer_modes={}))
    zs = next(s for s in res["signals"] if s["name"] == "zero_success")
    assert zs["status"] == OK


def test_feedback_human_review_spike_detected_when_present():
    res = evaluate_alerts(_snapshot(
        answer_modes={"grounded": 100},
        chat_feedback_total={"good": 2, "human_review_requested": 8},
    ))
    hr = next(s for s in res["signals"] if s["name"] == "human_review_rate")
    assert hr["status"] in {WARN, CRITICAL}


def test_rate_limited_and_auth_rejection_counts():
    res = evaluate_alerts(_snapshot(
        answer_modes={"grounded": 100},
        api_rate_limited_total={"authenticated": 30},
        api_auth_rejection_total={"invalid_credentials": 60},
    ))
    rl = next(s for s in res["signals"] if s["name"] == "rate_limited")
    ar = next(s for s in res["signals"] if s["name"] == "auth_rejection")
    assert rl["status"] == CRITICAL
    assert ar["status"] == CRITICAL


# --- 5. no secrets / keys / prompts / docs / .env in output ----------------


def test_alert_output_exposes_no_secrets():
    # Even if a (malformed) snapshot smuggled secret-looking strings as counter
    # names, the evaluator reads known metric names only and emits names+counts.
    res = evaluate_alerts(_snapshot(
        answer_modes={"grounded": 100},
        chat_provider_error_total={"timeout": 1},
    ))
    blob = json.dumps(res, ensure_ascii=False)
    for forbidden in ("sk-", "Bearer ", "API_AUTH_KEYS", "password", "OPENAI_API_KEY", "X-Api-Key"):
        assert forbidden not in blob
    # Signal names are a stable, safe enum set.
    for s in res["signals"]:
        assert s["name"] in {
            "error_rate", "fallback_rate", "guard_trip_rate", "zero_success",
            "rate_limited", "auth_rejection", "human_review_rate", "latency_p95_ms",
        }


def test_cli_checker_runs_and_exit_codes(tmp_path):
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps(_snapshot(
        answer_modes={"grounded": 50, "fallback": 50},
        chat_provider_error_total={"rate_limited": 30},
    )), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "alert_check.py"), str(snap)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 2  # CRITICAL
    assert "OVERALL: CRITICAL" in proc.stdout
    # No secret-like tokens in CLI output.
    for forbidden in ("sk-", "Bearer ", "X-Api-Key", "OPENAI_API_KEY"):
        assert forbidden not in proc.stdout


# --- 6. existing metrics/readiness behavior remains compatible -------------


def _setup_chat(monkeypatch, tmp_path):
    monkeypatch.delenv("API_AUTH_ENABLED", raising=False)
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", False)
    monkeypatch.setattr(config, "ANSWER_CACHE_ENABLED", False)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    answer_cache.clear()
    metrics_registry.reset()


def test_metrics_endpoint_shape_unchanged(monkeypatch, tmp_path):
    _setup_chat(monkeypatch, tmp_path)
    client = TestClient(main.app)
    payload = client.get("/metrics").json()
    assert set(payload.keys()) >= {"uptime_seconds", "total_requests", "error_requests", "counters"}
    # The evaluator consumes the live /metrics payload shape without error.
    res = evaluate_alerts(payload)
    assert "overall" in res and "signals" in res


def test_feedback_counter_increments_with_stable_label(monkeypatch, tmp_path):
    _setup_chat(monkeypatch, tmp_path)
    monkeypatch.setattr(main, "append_feedback_audit_event", lambda event: True)
    client = TestClient(main.app)

    resp = client.post(
        "/chat/feedback",
        json={"feedback_token": "ui-uuid", "feedback_type": "human_review_requested", "tenant_id": "default"},
    )
    assert resp.status_code == 200
    snap = metrics_registry.snapshot()
    assert snap["chat_feedback_total"] == {"human_review_requested": 1}
    # No raw key/token leaked into metrics labels.
    assert "ui-uuid" not in json.dumps(snap)
