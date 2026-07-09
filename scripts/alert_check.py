#!/usr/bin/env python3
"""Local-only alert checker (Prompt036).

Reads a /metrics JSON snapshot (from a file argument or stdin) and evaluates the
documented alert thresholds (see docs/operations.md "Alert thresholds"). Pure
local evaluation: no network, no Docker, no external Prometheus, no .env access,
no secrets. Prints per-signal OK/WARN/CRITICAL and exits 0 (OK) / 1 (WARN) /
2 (CRITICAL) so it is cron- and CI-friendly.

Usage:
  # from a saved snapshot
  curl -s http://127.0.0.1:8000/metrics > snap.json
  python scripts/alert_check.py snap.json

  # or via stdin
  curl -s http://127.0.0.1:8000/metrics | python scripts/alert_check.py -
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapi.alerting import evaluate_alerts, status_exit_code  # noqa: E402


def _load(source: str) -> dict:
    if source == "-":
        return json.loads(sys.stdin.read())
    return json.loads(Path(source).read_text(encoding="utf-8"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate local alert thresholds from a /metrics JSON snapshot.")
    parser.add_argument("snapshot", help="Path to a /metrics JSON snapshot, or '-' for stdin.")
    parser.add_argument("--json", action="store_true", help="Emit the full result as JSON.")
    args = parser.parse_args(argv)

    try:
        payload = _load(args.snapshot)
    except Exception as exc:  # noqa: BLE001
        print(f"alert_check: could not read snapshot: {type(exc).__name__}", file=sys.stderr)
        return 2

    result = evaluate_alerts(payload if isinstance(payload, dict) else {})

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"OVERALL: {result['overall']}  (chat_requests={result['chat_requests']}, "
              f"chat_successes={result['chat_successes']})")
        for s in result["signals"]:
            value = s.get("value")
            detail = s.get("detail")
            line = f"  [{s['status']}] {s['name']}"
            if value is not None:
                line += f" = {value}"
            if detail:
                line += f" ({detail})"
            print(line)

    return status_exit_code(result["overall"])


if __name__ == "__main__":
    raise SystemExit(main())
