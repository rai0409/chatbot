#!/usr/bin/env python3
"""Evaluate a /metrics snapshot and send alert notifications (Prompt051; 2026-11 ops hardening).

Default-off: if no Slack/SMTP env is configured this is a no-op. Reads a JSON
snapshot (file arg or stdin), evaluates thresholds, and routes WARN/CRITICAL to
Slack/email. Secrets are env-only and never printed. Exits with the alert
severity code (0 OK / 1 WARN / 2 CRITICAL).

  --previous FILE   evaluate the window between two snapshots (counter deltas,
                    webapi.alerting.window_payload) instead of the cumulative
                    counters of the process
  --state-dir DIR   de-duplicate: notify only when the overall status or the set
                    of non-OK signals changed since the last notification
                    (DIR/alert_state.json; scripts/monitoring_runner.sh passes
                    KURADEN_MONITOR_DIR). A return to OK is notified once when
                    the channels route OK.
  --failure REASON  no snapshot: notify a CRITICAL "monitoring failed" (the
                    runner could not fetch /metrics, or systemd OnFailure)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapi.alerting import CRITICAL, OK, evaluate_alerts, status_exit_code, window_payload  # noqa: E402
from webapi import notifications  # noqa: E402

STATE_FILE = "alert_state.json"


def _load(path: str):
    return json.loads(sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8"))


def _fingerprint(result) -> dict:
    bad = sorted(f"{s['name']}={s['status']}" for s in result.get("signals", []) if s.get("status") != OK)
    return {"overall": result.get("overall"), "signals": bad}


def state_file(failure=None) -> str:
    # A monitoring failure has its own state (the runner removes it when the failure is over).
    return f"alert_state_failure_{failure}.json" if failure else STATE_FILE


def should_notify(result, state_dir, failure=None) -> bool:
    """True when the alert state changed since the last notification (always True without a state dir)."""
    if not state_dir:
        return True
    path = Path(state_dir) / state_file(failure)
    try:
        last = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        last = None
    current = _fingerprint(result)
    if last == current:
        return False
    if last is None and current["overall"] == OK:
        _save_state(state_dir, current, failure)
        return False  # first run and all is well: nothing to say
    return True


def _save_state(state_dir, fingerprint, failure=None) -> None:
    directory = Path(state_dir)
    directory.mkdir(parents=True, exist_ok=True)
    name = state_file(failure)
    tmp = directory / f".{name}.tmp-{os.getpid()}"
    with tmp.open("w", encoding="utf-8") as out:
        out.write(json.dumps(fingerprint, ensure_ascii=False))
        out.flush()
        os.fsync(out.fileno())
    os.replace(tmp, directory / name)


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Evaluate a /metrics snapshot and notify on alerts.")
    parser.add_argument("snapshot", nargs="?", help="Path to a /metrics JSON snapshot, or '-' for stdin.")
    parser.add_argument("--previous", default=None, help="the previous snapshot: evaluate counter deltas")
    parser.add_argument("--state-dir", default=None, help="de-duplicate notifications (alert_state.json)")
    parser.add_argument("--failure", default=None, help="notify a monitoring failure instead of a snapshot")
    args = parser.parse_args(argv)
    reason = None
    if args.failure:
        reason = "".join(ch for ch in args.failure if ch.isalnum() or ch in "_-.")[:80] or "monitoring_failed"
        result = {"overall": CRITICAL, "chat_requests": 0, "chat_successes": 0,
                  "signals": [{"name": "monitoring_failure", "status": CRITICAL, "value": reason, "detail": reason}]}
    else:
        if not args.snapshot:
            parser.error("a snapshot (or --failure) is required")
        try:
            payload = _load(args.snapshot)
        except (OSError, ValueError) as exc:
            print(f"alert_notify: could not read snapshot: {type(exc).__name__}", file=sys.stderr)
            return 2
        previous = None
        if args.previous:
            try:
                previous = _load(args.previous)
            except (OSError, ValueError):  # no usable previous snapshot: cumulative counters
                previous = None
        payload = payload if isinstance(payload, dict) else {}
        result = evaluate_alerts(window_payload(payload, previous) if previous is not None else payload)

    if not should_notify(result, args.state_dir, reason):
        print(f"overall={result['overall']} (unchanged since the last notification)")
        return status_exit_code(result["overall"])
    if notifications.notify_enabled():
        summary = notifications.notify(result)
        # Print only the safe summary (channels + per-channel ok/attempts).
        print(json.dumps({"overall": summary["overall"], "channels": summary["channels"],
                          "results": summary.get("results", {})}, ensure_ascii=False))
        delivered = all((r or {}).get("ok", True) for r in (summary.get("results") or {}).values())
    else:
        print(f"overall={result['overall']} (notifications disabled: no channels configured)")
        delivered = True
    if args.state_dir and delivered:
        _save_state(args.state_dir, _fingerprint(result), reason)  # a failed delivery is retried on the next run
    return status_exit_code(result["overall"])


if __name__ == "__main__":
    raise SystemExit(main())
