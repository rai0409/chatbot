# Prompt051: Slack + Email Alert Notifications

Implementation report. Adds optional Slack-webhook + SMTP-email notifications for
the alert-checker output, with severity routing and retry/backoff. Default-off;
secrets are env-only and never logged. Stdlib only (urllib, smtplib) — no new
dependencies. Tests use mock transports (no real network).

## Files changed

- `webapi/notifications.py` (new) — `route_severity` (OK→none, WARN→Slack,
  CRITICAL→Slack+email), `build_message` (safe text: overall + WARN/CRITICAL
  signal names/values only), injectable `_default_slack_send` (urllib) /
  `_default_email_send` (smtplib starttls+login), `_with_retry`
  (3 attempts, exponential backoff, records only the exception *type*), and
  `notify(result, *, slack_send=, email_send=, sleep=)` — best-effort, never
  raises, returns a secret-free summary.
- `scripts/alert_notify.py` (new, executable) — CLI: read a `/metrics` snapshot
  → evaluate → notify; no-op when no channel is configured; prints only the safe
  summary; exits with the alert severity code.
- `tests/test_alert_notifications.py` (new).
- `docs/reports/prompt051_slack_email_alert_notifications.md`.

## Safety / no-secret result

- **Default-off**: with no `ALERT_SLACK_WEBHOOK` / `ALERT_SMTP_*` configured,
  `notify_enabled()` is False and `notify()` is a no-op (verified).
- **Secrets env-only**: webhook URL and SMTP password are read from env and
  **never** appear in the message, summary, or logs (verified — the configured
  `secret-token-xyz` / `smtp-password-xyz` are asserted absent). Retry records
  only the exception type, never a verbatim message that could echo a URL.
- **Payload is safe**: messages carry only the overall status + WARN/CRITICAL
  signal names/values (enum + numbers) — no prompts, document text, API keys, or
  tenant data (verified).
- **Severity routing + retry/backoff** verified with mock transports (retry-then-
  success records 3 attempts + 2 backoffs; all-fail returns not-ok without
  raising; CRITICAL sends both channels).

## Verification results

- `tests/test_alert_notifications.py` + `test_monitoring_alerts.py`: **23 passed**.
- Full suite: **832 passed, 0 failed** (+9). Full suite WAS run. No real network;
  mock senders only.

## Final judgment: PASS

## Next recommendation

Prompt052 — SLO/SLA + incident escalation runbook (docs).
