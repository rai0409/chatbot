# Prompt049: Prometheus + Grafana Observability Pack

You are working in:

/home/rai/chatbot
## Goal

Create a Prometheus + Grafana operational pack over the EXISTING safe aggregate
metrics (the /metrics Prometheus exposition and webapi/alerting.py thresholds).
No new runtime metrics that expose raw prompts, document text, API keys, trust
tokens, or tenant-private data.

## Scope

- A Prometheus scrape config (deploy/observability/prometheus.yml) targeting
  /metrics?format=prometheus, with the per-process caveat documented.
- Prometheus alert rules mirroring the documented thresholds (error/fallback/
  guard-trip/429/auth-rejection/zero-success) - aligned with webapi/alerting.py.
- A Grafana dashboard JSON (deploy/observability/grafana_dashboard.json) over
  those counters.
- Docs on how to wire them (no cloud, no Docker required to validate the files).

## Tests (tests/test_observability_pack.py)

Prove: dashboard JSON and alert-rule files are valid JSON/YAML and reference only
known safe counter names; no secret/tenant/prompt strings present; alert-rule
thresholds are consistent with webapi/alerting.py defaults.

## Verification

    python -m pytest tests/test_observability_pack.py tests/test_monitoring_alerts.py tests/test_observability_export.py -q
    python -m pytest -q

## Report

docs/reports/prompt049_prometheus_grafana_observability_pack.md


## Global safety constraints (apply to this prompt)

Do not read .env. Do not print or infer secrets. Do not use .env model names.
Do not use real customer data. Do not mutate the production/default vectorstore
or default collection except through an explicitly safe, tested staged workflow.
Do not run Docker (unless this prompt explicitly decides it is safe and necessary
for local-only validation). Do not deploy externally. Do not push remotely.
Do not weaken tenant authorization, tenant isolation, API key behavior, rate
limiting, or production_safe behavior. Do not change retrieval thresholds or
cross-encoder settings unless this prompt explicitly analyzes and justifies it
with tests. Do not expose API keys, SSO secrets, trust tokens, raw prompts, raw
document text, or tenant-private data in UI, logs, metrics, alerts, reports, or
tests. No new dependencies unless explicitly justified by this prompt. Leave
unrelated orphan files untouched (including previous market prompt/report
orphans). Preserve Prompt034 UI, Prompt035 Chroma where, Prompt036 monitoring,
and Prompt037 enterprise-auth behavior unless explicitly in this prompt's scope.

## Execution mode

Proceed autonomously. Run targeted tests first; run broader tests only when
targeted tests pass and runtime is reasonable; never fabricate test results; if
the full suite is not run, say so. Commit and tag only on PASS with a
prompt-scoped diff and no unrelated orphan changes. On FAIL/PARTIAL: no commit,
no tag; write a blocker report and stop.

## Commit/tag policy

PASS -> commit message "prompt049 prometheus grafana observability pack", tag "prompt049-prometheus-grafana-observability-pack".
PARTIAL/FAIL -> no commit, no tag; report blocker and the next command.

## Required final output

1. Preconditions  2. Implementation summary  3. Safety/no-secret-exposure result
4. Verification results (targeted first; state if full suite not run)
5. Docs/report path  6. Git diff summary  7. Commit/tag result
8. Final judgment PASS/PARTIAL/FAIL  9. Next recommendation
