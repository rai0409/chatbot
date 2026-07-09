# KuraDen Pilot — Kickoff Agenda & Onboarding Checklist

Operator/customer onboarding for a paid on-prem PoC. Placeholders only; no
secrets, no real names. Pairs with
`docs/sales/kuraden_paid_pilot_proposal_template.md`.

## Kickoff agenda (60–90 min)

1. Goals + success metrics agreement (Section 3 of the proposal).
2. Scope: one department, sanitized documents, on-prem, `production_safe`.
3. Environment: host, TLS/reverse proxy, optional IdP staging tenant.
4. Data handling: sanitization, alias usage, no cloud egress.
5. Roles: business owner, operator, human-in-the-loop reviewer.
6. Timeline + checkpoints (weekly), and the go/no-go criteria for annual.

## Onboarding checklist

- [ ] Host provisioned; venv + dependencies installed (`requirements.txt`).
- [ ] TLS terminated at the proxy; `:8000` not exposed directly.
- [ ] Env set: `API_AUTH_ENABLED=true` + per-tenant keys + `API_AUTH_TENANT_MAP`;
      `ADMIN_AUTH_ENABLED=true`; `RATE_LIMIT_ENABLED=true`;
      `SEARCH_DEBUG_ENABLED=false`.
- [ ] (Optional) SSO: reverse-proxy bridge or in-app OIDC
      (`docs/operations/sso_real_idp_validation_checklist.md`).
- [ ] Branding configured (`BRANDING_*`).
- [ ] Documents **sanitized**; dry-run onboarding into a non-production
      collection clean (no duplicate ids / tenant mismatch / collisions).
- [ ] Safe promotion plan approved (`scripts/promote_collection.py`).
- [ ] Backup taken; restore rehearsed (`scripts/backup.sh` / `restore.sh`).
- [ ] Monitoring wired + acceptance checklist signed
      (`docs/operations/monitoring_ops_acceptance_checklist.md`).
- [ ] PoC question set authored + validated
      (`scripts/poc_eval_check.py`); baseline eval run.
- [ ] `scripts/limited_beta_preflight.sh` exits 0.
- [ ] Rollback owner named (`docs/reports/limited_beta_rollback_runbook.md`).

## Weekly checkpoint

- Review measured metrics + reviewer notes; log incidents; adjust corpus.

## Go/no-go to annual

- Metrics meet agreed targets; no unresolved safety issues (error rate ~0);
  operations acceptance passed. Decision recorded via the PoC→annual report
  (Prompt065 template).
