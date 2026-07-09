#!/usr/bin/env python3
"""Evaluate a /metrics snapshot and send alert notifications (Prompt051).

Default-off: if no Slack/SMTP env is configured this is a no-op. Reads a JSON
snapshot (file arg or stdin), evaluates thresholds, and routes WARN/CRITICAL to
Slack/email. Secrets are env-only and never printed. Exits with the alert
severity code (0 OK / 1 WARN / 2 CRITICAL).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapi.alerting import evaluate_alerts, status_exit_code  # noqa: E402
from webapi import notifications  # noqa: E402


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Evaluate a /metrics snapshot and notify on alerts.")
    parser.add_argument("snapshot", help="Path to a /metrics JSON snapshot, or '-' for stdin.")
    args = parser.parse_args(argv)
    try:
        payload = json.loads(sys.stdin.read() if args.snapshot == "-" else Path(args.snapshot).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"alert_notify: could not read snapshot: {type(exc).__name__}", file=sys.stderr)
        return 2

    result = evaluate_alerts(payload if isinstance(payload, dict) else {})
    if notifications.notify_enabled():
        summary = notifications.notify(result)
        # Print only the safe summary (channels + per-channel ok/attempts).
        print(json.dumps({"overall": summary["overall"], "channels": summary["channels"],
                          "results": summary.get("results", {})}, ensure_ascii=False))
    else:
        print(f"overall={result['overall']} (notifications disabled: no channels configured)")
    return status_exit_code(result["overall"])


if __name__ == "__main__":
    raise SystemExit(main())
