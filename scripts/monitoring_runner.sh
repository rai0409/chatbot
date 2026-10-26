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
    exit 2
  fi
fi

# Retention: keep only the newest N snapshots.
mapfile -t SNAPS < <(ls -1t "$OUT_DIR/snapshots/"snapshot_*.json 2>/dev/null || true)
if (( ${#SNAPS[@]} > RETENTION )); then
  for old in "${SNAPS[@]:$RETENTION}"; do rm -f "$old"; done
fi

# Evaluate alerts.
set +e
"$PYTHON" scripts/alert_check.py "$SNAP_FILE" > "$OUT_DIR/last_result.txt" 2>&1
CODE=$?
set -e

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

exit "$CODE"
