# Monitoring Operations Acceptance Checklist (Synthetic Signals)

Proves the monitoring package works end to end in a **local, customer-like**
setup using **synthetic signals only** — no Docker required, no external network,
no secrets. Components: Prometheus pack (Prompt049), scheduled runner
(Prompt050), notifications (Prompt051), alert checker/exposition (Prompt036).

## Local wiring walkthrough (synthetic)

1. **Exposition**: `curl -s 'http://127.0.0.1:8000/metrics?format=prometheus'`
   returns the per-process counters (enum labels only).
2. **Scrape config + dashboard + rules**: `deploy/observability/prometheus.yml`,
   `grafana_dashboard.json`, `alert_rules.yml` (thresholds mirror
   `webapi/alerting.py`).
3. **Scheduled checker**: `scripts/monitoring_runner.sh --snapshot <snap.json>
   --out-dir runs/monitoring` snapshots `/metrics`, runs `scripts/alert_check.py`,
   retains N snapshots, and exits 0/1/2.
4. **Notification routing (synthetic)**: with `ALERT_SLACK_WEBHOOK` / `ALERT_SMTP_*`
   set to mock/test endpoints, `scripts/alert_notify.py <snap.json>` routes
   WARN→Slack, CRITICAL→Slack+email. (Tests use mock transports — no real send.)
5. **Escalation entrypoint**: a CRITICAL leads the operator to
   `docs/reports/prompt052_slo_sla_incident_escalation_runbook.md` +
   `limited_beta_rollback_runbook.md`.

## Acceptance checklist (sign off each)

- [ ] `/metrics?format=prometheus` serves; labels are enum-only (no keys/queries/
      doc text). Evidence: `tests/test_observability_export.py`.
- [ ] Alert rules + dashboard reference only known safe counters; thresholds
      match `alerting.py`. Evidence: `tests/test_observability_pack.py`.
- [ ] The runner produces the correct exit code on a synthetic CRITICAL
      snapshot, retains N snapshots, and logs `status=`/`exit=` (no secrets).
      Evidence: `tests/test_monitoring_runner.py`.
- [ ] Severity routing (OK→none, WARN→Slack, CRITICAL→Slack+email), retry/backoff,
      and **no secret in payload/logs**. Evidence: `tests/test_alert_notifications.py`.
- [ ] Log rotation + snapshot retention caps enforced.
- [ ] Escalation runbook reachable from a CRITICAL.

## What this proves vs not

- **Proves (synthetic):** the pieces are wired, alerts evaluate, notifications
  route, retention/rotation hold, and no secrets/tenant data leak — all locally.
- **Does NOT prove:** behavior under real production load, real Slack/SMTP
  delivery (use real env in the customer environment), or HA. Wire to the
  customer's real monitor/endpoints during onboarding before claiming
  "monitoring proven in production".
