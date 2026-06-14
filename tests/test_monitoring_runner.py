from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "monitoring_runner.sh"
DEPLOY = ROOT / "deploy" / "monitoring"


_SNAP_SEQ = [0]


def _snap(tmp_path, counters):
    _SNAP_SEQ[0] += 1
    p = tmp_path / f"snap_{_SNAP_SEQ[0]}.json"
    p.write_text(json.dumps({"counters": counters}), encoding="utf-8")
    return p


def _run(snapshot, out_dir, retention=5):
    return subprocess.run(
        ["bash", str(RUNNER), "--snapshot", str(snapshot), "--out-dir", str(out_dir), "--retention", str(retention)],
        capture_output=True, text=True,
    )


def test_runner_exit_codes_match_alert_severity(tmp_path):
    out = tmp_path / "out"
    ok = _snap(tmp_path, {"chat_answer_mode_total": {"grounded": 100}})
    crit = _snap(tmp_path, {"chat_answer_mode_total": {"grounded": 50, "fallback": 50},
                            "chat_provider_error_total": {"timeout": 30}})
    assert _run(ok, out).returncode == 0
    assert _run(crit, out).returncode == 2


def test_runner_writes_log_and_snapshots(tmp_path):
    out = tmp_path / "out"
    snap = _snap(tmp_path, {"chat_answer_mode_total": {"grounded": 100}})
    _run(snap, out)
    assert (out / "monitor.log").is_file()
    assert list((out / "snapshots").glob("snapshot_*.json"))
    log = (out / "monitor.log").read_text(encoding="utf-8")
    assert "status=" in log and "exit=" in log


def test_runner_retention_cap(tmp_path):
    out = tmp_path / "out"
    snap = _snap(tmp_path, {"chat_answer_mode_total": {"grounded": 100}})
    for _ in range(6):
        _run(snap, out, retention=3)
    kept = list((out / "snapshots").glob("snapshot_*.json"))
    assert len(kept) == 3


def test_runner_output_has_no_secrets(tmp_path):
    out = tmp_path / "out"
    snap = _snap(tmp_path, {"chat_answer_mode_total": {"grounded": 100},
                            "api_auth_rejection_total": {"invalid_credentials": 1}})
    proc = _run(snap, out)
    blob = proc.stdout + proc.stderr + (out / "monitor.log").read_text(encoding="utf-8")
    for forbidden in ("sk-", "Bearer ", "X-Api-Key", "OPENAI_API_KEY", "OIDC_CLIENT_SECRET"):
        assert forbidden not in blob


# --- unit/timer/cron file structure ----------------------------------------


def test_systemd_units_present_and_structured():
    svc = (DEPLOY / "kuraden-monitor.service").read_text(encoding="utf-8")
    tmr = (DEPLOY / "kuraden-monitor.timer").read_text(encoding="utf-8")
    cron = (DEPLOY / "cron.example").read_text(encoding="utf-8")
    for section in ("[Unit]", "[Service]", "[Install]"):
        assert section in svc
    assert "ExecStart=" in svc and "monitoring_runner.sh" in svc
    for section in ("[Unit]", "[Timer]", "[Install]"):
        assert section in tmr
    assert "OnUnitActiveSec=" in tmr
    assert "monitoring_runner.sh" in cron


def test_runner_is_executable():
    import os
    assert os.access(RUNNER, os.X_OK)
