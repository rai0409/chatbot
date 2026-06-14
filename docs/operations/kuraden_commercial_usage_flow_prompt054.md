# KuraDen — Commercial Usage Flow (Operator Guide)

Practical, step-by-step operator/customer guide. Not marketing. Placeholders
only; never commit real secrets. Deeper references are linked at the end.

## 0. Setup assumptions

- On-prem / closed-network host you control; the app serves plain HTTP on
  `:8000` behind a TLS-terminating reverse proxy (never expose `:8000`).
- Python venv with dependencies installed (`requirements.txt`, incl. Authlib +
  cryptography for OIDC).
- Synthetic or sanitized documents only for pilots — **no real customer data**
  until the data agreement permits.
- Secrets (`*_TOKEN`, `*_SECRET`, `*_KEYS`, webhook/SMTP creds) come from the
  process environment, never from committed files.

## 1. Admin / operator responsibilities

- Configure auth, tenants, and (optionally) SSO before exposing the app.
- Run document ingestion **dry-runs** and review the import manifest before any
  ingest; ingest only into an explicit **non-production** collection.
- Wire monitoring (scrape `/metrics`, install the timer) and alert routing.
- Take a backup before launch; rehearse restore.
- Own incident response during business hours (see runbooks).

## 2. Enable auth & tenants (do this first)

```bash
API_AUTH_ENABLED=true
API_AUTH_KEYS=<PILOT_KEY_1>,<PILOT_KEY_2>
API_AUTH_TENANT_MAP=<PILOT_KEY_1>=tenant_a,<PILOT_KEY_2>=tenant_b
ADMIN_AUTH_ENABLED=true
ADMIN_AUTH_TOKEN=<ADMIN_TOKEN>
RATE_LIMIT_ENABLED=true
SEARCH_DEBUG_ENABLED=false
```

Each pilot key maps to only its tenant(s); unmapped keys fail closed (403).

## 3. SSO setup (high-level; optional, default-off)

Two supported paths (full guide: `docs/reports/prompt048_entra_okta_reverse_proxy_tls_deployment_guides.md`):

- **Reverse-proxy bridge** — an IdP-connected proxy authenticates, strips
  client-supplied `X-Enterprise-*` headers, injects trusted ones + a shared
  trust token (`ENTERPRISE_AUTH_*`).
- **In-app OIDC** — `ENTERPRISE_OIDC_ENABLED=true` + `OIDC_ISSUER/CLIENT_ID/
  CLIENT_SECRET/REDIRECT_URI/JWKS_URI/SESSION_SECRET`, with
  `OIDC_GROUP_TENANT_MAP` / `OIDC_GROUP_ROLE_MAP` for RBAC. Login at
  `/auth/oidc/login`.

> Limitation: the OIDC code path is **mock-tested only**. Validate end-to-end
> against your real IdP tenant in staging before production.

## 4. Branding (optional)

```bash
BRANDING_PRODUCT_NAME="<顧客名> ナレッジ"
BRANDING_SUBTITLE="社内文書アシスタント"
BRANDING_THEME_COLOR="#0b6b5b"
```

Applied via `GET /branding` to the workspace at `/chat-ui`.

## 5. Document ingestion flow (dry-run first)

1. Prepare canonical chunk JSONL (synthetic/sanitized).
2. Validate (no ingest) via the admin UI `/admin/ingestion` or:
   ```bash
   .venv/bin/python scripts/onboard_documents_dry_run.py --input-dir <dir> --tenant-id <tenant>
   ```
3. Review the import manifest for `duplicate_ids` / `tenant_mismatches` /
   `collisions`. Job status is visible in the admin ingestion page.
4. Ingest **only** into an explicit non-production collection (the tooling
   refuses the production/default collection).

> A guarded promotion-to-served workflow is not yet built; treat go-live as a
> reviewed, manual step for now.

## 6. End-user chat flow

- Users open `/chat-ui`, type a question, and receive a streamed answer with a
  **citations panel**; if evidence is weak the system shows a calm
  **"分かりません"** message instead of guessing.
- Feedback buttons (good / bad / 人に確認したい) post to `/chat/feedback`.
- Conversation history (default-off) is per-user/tenant-isolated when enabled
  (`CONVERSATION_HISTORY_ENABLED=true`).

## 7. Monitoring flow

```bash
# Prometheus scrape + dashboard + rules
deploy/observability/prometheus.yml
deploy/observability/alert_rules.yml
deploy/observability/grafana_dashboard.json

# scheduled local checker (systemd timer or cron)
deploy/monitoring/kuraden-monitor.service
deploy/monitoring/kuraden-monitor.timer
scripts/monitoring_runner.sh        # snapshots /metrics, runs alert_check.py
```

Metrics are **per-process**; aggregate across workers in the scraper. `/health`
is the liveness probe.

## 8. Alert response flow

- `scripts/alert_check.py` exits 0/1/2 (OK/WARN/CRITICAL); the runner logs the
  status and (optionally) `scripts/alert_notify.py` routes WARN→Slack,
  CRITICAL→Slack+email (`ALERT_SLACK_WEBHOOK` / `ALERT_SMTP_*`, env-only).
- On CRITICAL: follow `docs/reports/prompt052_slo_sla_incident_escalation_runbook.md`
  and, for containment/rollback, `docs/reports/limited_beta_rollback_runbook.md`.

## 9. Backup / restore

```bash
bash scripts/backup.sh --output-dir backups
bash scripts/restore.sh backups/chatbot_backup_<TS>.tar.gz --target /tmp/restore_check
```

Restore always verifies the embedded sha256 manifest.

## 10. Known limitations (read before promising anything)

- Single-node; **no HA / failover / 24×7**.
- OIDC/RBAC and notifications are **mock-tested only** — validate with real
  IdP/endpoints in staging.
- Manufacturing-domain **accuracy is unmeasured** until the validation pack runs.
- Guarded production collection **promotion** is not yet built.

## 11. Deeper docs in the repo

- Ops baseline: `docs/operations.md`
- SSO deployment: `docs/reports/prompt048_entra_okta_reverse_proxy_tls_deployment_guides.md`
- SLO/SLA + incidents: `docs/reports/prompt052_slo_sla_incident_escalation_runbook.md`
- Launch/rollback/onboarding: `docs/reports/limited_beta_launch_checklist.md`,
  `limited_beta_rollback_runbook.md`, `pilot_tenant_onboarding_runbook.md`
- Acceptance gate: `docs/reports/prompt053_commercial_production_acceptance_gate.md`
