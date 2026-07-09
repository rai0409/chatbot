# KuraDen Support Operations & Incident Pack

Support-operations package for a first annual contract. **Default = business-hours
support; 24×7 is a clearly-marked FUTURE/OPTIONAL tier** (no staffed 24×7 on-call
evidence today). Placeholders only; no secrets, no real names.

## Severity definitions

- **SEV-1**: service down (`/health` failing) or confirmed cross-tenant exposure.
- **SEV-2**: degraded quality (sustained high error/fallback) or an auth-rejection
  burst; partial functionality lost.
- **SEV-3**: single-user issue, cosmetic, or non-blocking question.

## Response-time targets (business hours, JST Mon–Fri — proposed)

| Severity | Acknowledge | Update cadence | Notes |
| --- | --- | --- | --- |
| SEV-1 | within 1 business hour | hourly | single-node; manual recovery |
| SEV-2 | within 4 business hours | daily | triage with monitoring pack |
| SEV-3 | next business day | as needed | backlog |

> 24×7 / faster targets are a **FUTURE/OPTIONAL** tier requiring a staffed
> on-call rotation that does not exist yet. Do not promise it.

## Escalation matrix

1. Alert (Prometheus rule / `alert_check.py` CRITICAL → Slack/email) → on-call
   operator acknowledges, opens an incident note.
2. SEV-1 → `docs/reports/limited_beta_rollback_runbook.md` (contain, rotate keys,
   restore, revert tag, re-smoke).
3. SEV-2 → tune/triage via `docs/operations/monitoring_ops_acceptance_checklist.md`.
4. Unresolved within the tier window → escalate to the engineering owner.

## Staffing assumptions

- One operator (business hours) + one engineering escalation contact.
- Customer provides first-line triage for environment/IdP/proxy issues.
- 24×7 would require a multi-person rotation (out of current scope).

## Customer communication templates (placeholders only)

> Subject: [<PROGRAM>] <SEV> incident — <UTC_TIMESTAMP>
> We are investigating <SHORT_NEUTRAL_DESCRIPTION> affecting <SCOPE>. Status:
> <INVESTIGATING/IDENTIFIED/MONITORING/RESOLVED>. Next update by <UTC_TIMESTAMP>.

> Subject: [<PROGRAM>] Resolved — <UTC_TIMESTAMP>
> The incident affecting <SCOPE> is resolved. Cause: <BRIEF_NON_SENSITIVE>.
> Follow-ups: <ITEMS>.

No secrets, raw keys, real tenant identifiers, or another customer's information
in any communication.

## Runbook links

- Incident escalation / SLO-SLA: `docs/reports/prompt052_slo_sla_incident_escalation_runbook.md`
- Rollback: `docs/reports/limited_beta_rollback_runbook.md`
- Monitoring acceptance: `docs/operations/monitoring_ops_acceptance_checklist.md`
- DR: `docs/operations/dr_drill_and_recovery_objectives.md`

## Incident review template

- Incident id / date / severity / duration
- Trigger + timeline (UTC)
- Root cause (non-sensitive)
- Affected tenants (alias) / audit window reviewed
- Actions taken / rollback used
- Follow-ups + owners
- Customer communications sent
