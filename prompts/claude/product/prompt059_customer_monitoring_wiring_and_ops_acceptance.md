# Prompt059: Customer Monitoring Wiring & Ops Acceptance

You are working in:

/home/rai/chatbot
## Context

The observability pack (Prompt049 Prometheus/Grafana), scheduled runner
(Prompt050 systemd/cron), and notifications (Prompt051 Slack/email) exist but
are not yet proven wired together in a customer-like environment with synthetic
signals.

## Goal

Prove the monitoring package works end-to-end in a LOCAL customer-like setup
using SYNTHETIC signals only, and produce an operations acceptance checklist for
alerts, dashboards, notification routing, log retention, and escalation
entrypoints.

## Scope

- A local wiring walkthrough (scrape /metrics, run alert_check/runner, route a
  synthetic CRITICAL to mock Slack/email) and an acceptance checklist.
- Verify retention/rotation and that no secrets/tenant data appear in
  metrics/alerts (reuse existing tests' assertions).
- No Docker required; no external network (mock transports/synthetic snapshots).

## Required deliverables

- docs/operations/ acceptance checklist + docs/reports/prompt059_customer_monitoring_wiring_and_ops_acceptance.md.
- Tests/checks reusing test_observability_pack / test_monitoring_runner /
  test_alert_notifications; synthetic only.

## Tests / checks

    python -m pytest tests/test_observability_pack.py tests/test_monitoring_runner.py tests/test_alert_notifications.py -q
    python -m pytest --collect-only -q


## Execution mode

Proceed autonomously. Do not ask for yes/no confirmation. Run targeted
tests/checks first; run broader tests only when targeted checks pass and runtime
is reasonable; never fabricate test results; if the full suite is not run, say
so. Commit and tag only on PASS with a prompt-scoped diff and no unrelated orphan
changes. On FAIL/PARTIAL: no commit, no tag; write a blocker report and stop.

## Safety constraints

Do not read .env. Do not print or infer secrets. Do not use .env model names.
Do not use real customer data. Do not mutate the production/default vectorstore.
Do not run Docker. Do not deploy externally. Do not push remotely. Do not change
product runtime behavior unless this prompt's scope explicitly and safely
requires it with tests. Do not weaken tenant authorization, tenant isolation,
API key behavior, OIDC/session behavior, RBAC behavior, rate limiting, or
production_safe behavior. Do not change retrieval thresholds or cross-encoder
settings unless explicitly analyzed, justified, and tested. Do not expose API
keys, OIDC secrets, session secrets, trust tokens, raw prompts, raw document
text, tenant-private data, or customer-private data in reports, docs, tests,
prompts, metrics, alerts, or artifacts. No new dependencies unless explicitly
justified. Leave unrelated orphan files untouched (including
docs/reports/japan_rag_market_positioning_after_prompt030.md and
prompts/claude/market/). Preserve all completed behavior from Prompts034-054.

## Conservative no-overclaim requirement

Be strict and evidence-based. Do not claim production readiness, accuracy
guarantees, HA, 24x7 SLA, compliance certification, or competitor superiority.
Separate mock-tested / synthetic-data evidence from anything that requires a real
customer environment, real IdP tenant, or real documents, and label each clearly.

## Commit/tag policy

PASS -> commit message "prompt059 customer monitoring wiring and ops acceptance", tag "prompt059-customer-monitoring-wiring-and-ops-acceptance".
PARTIAL/FAIL -> no commit, no tag; report the blocker and the next command.

## Required final output

1. Preconditions  2. Implementation/analysis summary  3. Safety / no-secret /
no-customer-data result  4. Verification results (targeted first; state if full
suite not run)  5. Deliverable paths  6. Git diff summary  7. Commit/tag result
8. Final judgment PASS/PARTIAL/FAIL  9. Next recommendation
