# Prompt051: Slack + Email Alert Notifications

You are working in:

/home/rai/chatbot
## Goal

Add optional Slack webhook + SMTP email notification for the alert checker
output, with severity routing and retry/backoff. Secrets (webhook URL, SMTP
creds) are ENV-ONLY and never logged. Tests use synthetic endpoints / mocks only
(no real network).

## Scope

- A notifier module: given an alert result (from webapi/alerting.py), route by
  severity (e.g. WARN -> Slack, CRITICAL -> Slack+email) with retry/backoff.
- Default-off; enabled via env (ALERT_SLACK_WEBHOOK / ALERT_SMTP_*); missing
  config is a no-op (no crash). Prefer stdlib (urllib, smtplib) to avoid new
  deps; justify any dependency.
- Never log secret values; redact in any debug output.

## Tests (tests/test_alert_notifications.py)

Prove: severity routing; retry/backoff on transient failure (mocked); no secret
value in logs/output; default-off no-op when unconfigured; payload contains only
safe enum/aggregate fields (no prompts/docs/keys/tenant data).

## Verification

    python -m pytest tests/test_alert_notifications.py tests/test_monitoring_alerts.py -q
    python -m pytest -q

## Report

docs/reports/prompt051_slack_email_alert_notifications.md


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

PASS -> commit message "prompt051 slack email alert notifications", tag "prompt051-slack-email-alert-notifications".
PARTIAL/FAIL -> no commit, no tag; report blocker and the next command.

## Required final output

1. Preconditions  2. Implementation summary  3. Safety/no-secret-exposure result
4. Verification results (targeted first; state if full suite not run)
5. Docs/report path  6. Git diff summary  7. Commit/tag result
8. Final judgment PASS/PARTIAL/FAIL  9. Next recommendation
