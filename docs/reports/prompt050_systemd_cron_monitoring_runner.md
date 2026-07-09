# Prompt050: systemd / cron Monitoring Runner

Implementation report. Adds a local-only periodic runner that snapshots
`/metrics`, runs the alert checker, retains a bounded number of snapshots, and
appends a size-rotated log — wired via a systemd service+timer with a cron
fallback.

## Files changed

- `scripts/monitoring_runner.sh` (new, executable) — fetches `/metrics` JSON
  (or uses `--snapshot`), runs `scripts/alert_check.py`, exits with its code
  (0 OK / 1 WARN / 2 CRITICAL), retains the newest N snapshots
  (`--retention` / `KURADEN_MONITOR_RETENTION`), and appends a size-rotated
  `monitor.log`. No `.env`, no Docker, no external network beyond local
  `/metrics`; no secrets in output.
- `deploy/monitoring/kuraden-monitor.service` + `.timer` (new) — oneshot unit +
  5-minute timer; `SuccessExitStatus=0 1 2` so WARN/CRITICAL don't fail the unit
  (alerting is the signal).
- `deploy/monitoring/cron.example` (new) — 5-minute cron fallback.
- `tests/test_monitoring_runner.py` (new).
- `docs/reports/prompt050_systemd_cron_monitoring_runner.md`.

## Behavior / safety

- Exit code mirrors alert severity (verified). Snapshot retention cap enforced
  (verified: 6 runs, cap 3 → 3 kept; unique filenames avoid same-second
  collisions). Log carries only `status=` + `exit=` (no secrets, verified).
- systemd/cron files contain the required sections and reference the runner
  (verified). Runner is executable (verified).

## Verification results

- `tests/test_monitoring_runner.py` + `test_monitoring_alerts.py`: **6** new +
  monitoring all pass.
- Full suite: **823 passed, 0 failed** (+6). `limited_beta_preflight.sh` exit 0.
  Full suite WAS run. No Docker/systemd needed to validate (runner driven with
  a synthetic snapshot; unit/cron files validated structurally).

## Final judgment: PASS

## Next recommendation

Prompt051 — Slack + email alert notifications (env-only secrets, mock tests).
