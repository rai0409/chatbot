# Prompt050: systemd / cron Monitoring Runner

You are working in:

/home/rai/chatbot
## Goal

Create a systemd service+timer (and a cron fallback) that periodically captures a
/metrics snapshot and runs scripts/alert_check.py, with log rotation and snapshot
retention, for on-prem operators. Local-only; no Docker, no deploy.

## Scope

- Unit/timer files (deploy/monitoring/*.service, *.timer) + a cron example, plus
  a small wrapper script that fetches /metrics locally, runs alert_check.py, and
  writes a rotated log + retained snapshots under a configurable dir.
- Log rotation + retention (count/age cap); env-only config; no secrets in logs.
- Local install docs; no external network beyond the local /metrics endpoint.

## Tests (tests/test_monitoring_runner.py)

Prove: the wrapper runs against a synthetic snapshot and exits with the alert
checker's code; rotation/retention caps enforced; unit/timer/cron files parse/
lint as expected; no secret in outputs.

## Verification

    python -m pytest tests/test_monitoring_runner.py tests/test_monitoring_alerts.py -q
    python -m pytest -q

## Report

docs/reports/prompt050_systemd_cron_monitoring_runner.md


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

PASS -> commit message "prompt050 systemd cron monitoring runner", tag "prompt050-systemd-cron-monitoring-runner".
PARTIAL/FAIL -> no commit, no tag; report blocker and the next command.

## Required final output

1. Preconditions  2. Implementation summary  3. Safety/no-secret-exposure result
4. Verification results (targeted first; state if full suite not run)
5. Docs/report path  6. Git diff summary  7. Commit/tag result
8. Final judgment PASS/PARTIAL/FAIL  9. Next recommendation
