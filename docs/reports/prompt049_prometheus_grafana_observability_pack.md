# Prompt049: Prometheus + Grafana Observability Pack

Implementation report. Adds a Prometheus scrape config, alert rules, and a
Grafana dashboard over the EXISTING safe aggregate counters. No new runtime
metrics; no secrets/tenant/prompt/document data exposed.

## Files changed

- `deploy/observability/prometheus.yml` (new) — scrapes
  `/metrics?format=prometheus`; documents the per-process counter caveat.
- `deploy/observability/alert_rules.yml` (new) — PromQL alert rules mirroring
  `webapi/alerting.py` `DEFAULT_THRESHOLDS` and `docs/operations.md`
  (error/fallback/guard-trip rates; 429 + auth-rejection counts; target-down).
- `deploy/observability/grafana_dashboard.json` (new) — 8-panel dashboard over
  the same counters (answer modes, error/fallback/guard rates, 429,
  auth-rejections, feedback, target up).
- `tests/test_observability_pack.py` (new).
- `docs/reports/prompt049_prometheus_grafana_observability_pack.md`.

## Safety result

- The pack references **only known, safe metric names** (enforced by a test
  against the app's actual counter set) and contains **no secrets or tenant
  data** (scanned for client secrets, trust tokens, admin tokens, API keys, and
  tenant identifiers). The scrape target is `/metrics`, which is unauthenticated
  by design and carries only stable enum labels.
- Alert-rule critical thresholds are asserted to match the
  `webapi/alerting.py` defaults so the local checker and Prometheus agree.

## Verification results

- `tests/test_observability_pack.py` + `test_monitoring_alerts.py` +
  `test_observability_export.py`: **32 passed**.
- Full suite: **817 passed, 0 failed** (+6). Full suite WAS run.
- No Docker/Prometheus/Grafana process required to validate the files (pure
  JSON/YAML parsing + content checks).

## Final judgment: PASS

## Next recommendation

Prompt050 — systemd/cron monitoring runner (periodic health/metrics/alert checks).
