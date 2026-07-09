# Prompt059: Customer Monitoring Wiring & Ops Acceptance

Proves the monitoring package works end to end in a **local, customer-like**
setup using **synthetic signals only**, and provides an operations acceptance
checklist. No Docker, no external network, no secrets; no product runtime change.

## Implementation summary

- `docs/operations/monitoring_ops_acceptance_checklist.md` (new) — local wiring
  walkthrough (exposition → scrape/dashboard/rules → scheduled runner →
  notification routing → escalation) and a sign-off acceptance checklist mapping
  each item to its proving test, plus an honest "proves vs not" note.
- This report.

No new code: the monitoring components (Prompt036/049/050/051) already exist and
are reused.

## Verification results (synthetic only)

- `tests/test_observability_pack.py` + `test_monitoring_runner.py` +
  `test_alert_notifications.py` + `test_observability_export.py`: **33 passed**.
- Synthetic end-to-end: `monitoring_runner.sh` exits **2** on a CRITICAL snapshot;
  `alert_notify.py` is a **no-op** when no channels are configured (correct
  default-off). `--collect-only`: **850 collected**. Full suite **not run** for
  this docs/acceptance prompt (no product source change; reuses already-green
  monitoring suites).

## What is NOT validated externally

- Real Slack/SMTP delivery, real Prometheus/Grafana scrape, and behavior under
  production load require the customer's environment — wire there during
  onboarding before claiming "monitoring proven in production".

## Final judgment: PASS

## Next recommendation

Prompt060 — paid pilot sales / contract / onboarding pack (docs).
