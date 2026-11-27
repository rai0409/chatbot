#!/usr/bin/env bash
# Periodic monitoring runner (Prompt050).
#
# Captures a /metrics JSON snapshot (or uses a provided one), runs the local
# alert checker (scripts/alert_check.py), retains a bounded number of snapshots,
# and appends a size-rotated log line. Exits with the alert checker's code
# (0 OK / 1 WARN / 2 CRITICAL) so systemd/cron can react.
#
# Optional (stage 9): KURADEN_METRICS_TOKEN (or METRICS_TOKEN) is sent as a
# bearer header (production /metrics requires it); KURADEN_FRESHNESS_MANIFEST
# runs scripts/check_freshness.py (action needed -> WARN); KURADEN_VERIFY_AUDIT
# =true runs scripts/verify_audit_chain.py (broken chain -> CRITICAL). The
# exit code is the worst of all checks.
#
# 2026-11 ops hardening:
#   - alerts are evaluated over the window since the previous snapshot (counter
#     deltas, webapi.alerting.window_payload); KURADEN_ALERT_WINDOW=cumulative
#     restores the old behaviour. The /metrics "ops" section (stuck collections,
#     interrupted promotions, ledger chain, fingerprint, backup age, disk) is
#     evaluated as current state.
#   - WARN/CRITICAL and failures (metrics not reachable, checker crash) are sent
#     through scripts/alert_notify.py, de-duplicated by state change
#     (KURADEN_MONITOR_DIR/alert_state.json). KURADEN_NOTIFY=false turns it off.
#
# Local-only: no Docker, no deploy, no external network beyond the local
# /metrics endpoint. Never reads .env; never prints secrets (the snapshot and
# checker output carry only safe aggregate metrics).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -x ".venv/bin/python" ]]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="python"
fi

METRICS_URL="${KURADEN_METRICS_URL:-http://127.0.0.1:8000/metrics}"
OUT_DIR="${KURADEN_MONITOR_DIR:-runs/monitoring}"
RETENTION="${KURADEN_MONITOR_RETENTION:-48}"
LOG_MAX_BYTES="${KURADEN_MONITOR_LOG_MAX_BYTES:-1048576}"
SNAPSHOT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --snapshot) SNAPSHOT="$2"; shift 2 ;;
    --metrics-url) METRICS_URL="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --retention) RETENTION="$2"; shift 2 ;;
    -h|--help) echo "usage: monitoring_runner.sh [--snapshot FILE] [--metrics-url URL] [--out-dir DIR] [--retention N]"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "$OUT_DIR/snapshots"

notify_failure() {
  # Best effort: a notification failure never changes the exit code.
  if [[ "${KURADEN_NOTIFY:-true}" != "false" ]]; then
    "$PYTHON" scripts/alert_notify.py --failure "$1" --state-dir "$OUT_DIR" >> "$OUT_DIR/notify.log" 2>&1 || true
  fi
}
TS="$(date -u +%Y%m%dT%H%M%SZ)"
# Unique suffix so rapid successive runs (e.g. tests) do not collide within the
# same second; production runs at minute intervals.
SNAP_FILE="$OUT_DIR/snapshots/snapshot_${TS}_${RANDOM}${RANDOM}.json"

# Acquire the snapshot.
if [[ -n "$SNAPSHOT" ]]; then
  cp "$SNAPSHOT" "$SNAP_FILE"
else
  METRICS_BEARER="${KURADEN_METRICS_TOKEN:-${METRICS_TOKEN:-}}"
  CURL_CONFIG=""
  if [[ -n "$METRICS_BEARER" ]]; then
    # Passed on stdin, not argv, so the token never shows in the process list.
    CURL_CONFIG="header = \"Authorization: Bearer ${METRICS_BEARER}\""
  fi
  if ! printf '%s\n' "$CURL_CONFIG" | curl -K - -fsS "$METRICS_URL" -o "$SNAP_FILE" 2>/dev/null; then
    echo "${TS} ERROR could_not_fetch_metrics" >> "$OUT_DIR/monitor.log"
    rm -f "$SNAP_FILE"
    notify_failure could_not_fetch_metrics
    exit 2
  fi
fi

clear_failure() { rm -f "$OUT_DIR/alert_state_failure_$1.json"; }
clear_failure could_not_fetch_metrics

# The previous snapshot (newest other than this one): the alert window starts there.
PREV="$(ls -1t "$OUT_DIR/snapshots/"snapshot_*.json 2>/dev/null | grep -vxF "$SNAP_FILE" | head -n1 || true)"
EVAL_FILE="$SNAP_FILE"
if [[ -n "$PREV" && "${KURADEN_ALERT_WINDOW:-delta}" != "cumulative" ]]; then
  EVAL_FILE="$OUT_DIR/window.json"
  if ! "$PYTHON" - "$SNAP_FILE" "$PREV" "$EVAL_FILE" <<'PY' 2>>"$OUT_DIR/monitor.log"
import json, sys
from pathlib import Path
sys.path.insert(0, ".")
from webapi.alerting import window_payload
cur = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
try:
    prev = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
except (OSError, ValueError):
    prev = None
Path(sys.argv[3]).write_text(json.dumps(window_payload(cur, prev)), encoding="utf-8")
PY
  then
    EVAL_FILE="$SNAP_FILE"
  fi
fi

# Retention: keep only the newest N snapshots.
mapfile -t SNAPS < <(ls -1t "$OUT_DIR/snapshots/"snapshot_*.json 2>/dev/null || true)
if (( ${#SNAPS[@]} > RETENTION )); then
  for old in "${SNAPS[@]:$RETENTION}"; do rm -f "$old"; done
fi

# Evaluate alerts.
set +e
"$PYTHON" scripts/alert_check.py "$EVAL_FILE" > "$OUT_DIR/last_result.txt" 2>&1
CODE=$?
set -e
if (( CODE > 2 )); then
  # The checker itself failed (not a severity): CRITICAL and a notification.
  echo "EXTRA: alert_check failed (exit ${CODE})" >> "$OUT_DIR/last_result.txt"
  notify_failure alert_check_failed
  CODE=2
else
  clear_failure alert_check_failed
fi

# Optional extra checks; their output goes to last_result.txt (no secrets).
if [[ -n "${KURADEN_FRESHNESS_MANIFEST:-}" ]]; then
  if ! "$PYTHON" scripts/check_freshness.py --manifest "$KURADEN_FRESHNESS_MANIFEST" >> "$OUT_DIR/last_result.txt" 2>&1; then
    echo "EXTRA: freshness WARN" >> "$OUT_DIR/last_result.txt"
    if (( CODE < 1 )); then CODE=1; fi
  fi
fi
if [[ "${KURADEN_VERIFY_AUDIT:-}" == "true" ]]; then
  if ! "$PYTHON" scripts/verify_audit_chain.py >> "$OUT_DIR/last_result.txt" 2>&1; then
    echo "EXTRA: audit_chain CRITICAL" >> "$OUT_DIR/last_result.txt"
    CODE=2
  fi
fi

OVERALL="$(grep -m1 '^OVERALL:' "$OUT_DIR/last_result.txt" | awk '{print $2}' || echo UNKNOWN)"

# Size-rotated log (no secrets: status + code only).
LOG="$OUT_DIR/monitor.log"
if [[ -f "$LOG" ]]; then
  SIZE=$(wc -c < "$LOG" 2>/dev/null || echo 0)
  if (( SIZE > LOG_MAX_BYTES )); then mv "$LOG" "$LOG.1"; fi
fi
echo "${TS} status=${OVERALL} exit=${CODE}" >> "$LOG"

# Notify on WARN/CRITICAL (and once on recovery), only when the state changed since the last notification.
if [[ "${KURADEN_NOTIFY:-true}" != "false" ]]; then
  NOTIFY_ARGS=("$SNAP_FILE" --state-dir "$OUT_DIR")
  [[ -n "$PREV" && "${KURADEN_ALERT_WINDOW:-delta}" != "cumulative" ]] && NOTIFY_ARGS+=(--previous "$PREV")
  "$PYTHON" scripts/alert_notify.py "${NOTIFY_ARGS[@]}" >> "$OUT_DIR/notify.log" 2>&1 || true
  if [[ "$CODE" == "2" ]] && grep -q '^EXTRA:' "$OUT_DIR/last_result.txt"; then
    # Extra checks (audit chain, freshness) are not in the snapshot: notify them as a monitoring failure.
    notify_failure extra_check_failed
  else
    clear_failure extra_check_failed
  fi
fi

exit "$CODE"
