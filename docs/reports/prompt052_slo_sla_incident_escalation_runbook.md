# Prompt052: SLO / SLA & Incident Escalation Runbook

Operational runbook for first paid annual contract discussions. **Honest by
design**: the product is single-node today with **no 24/7 staffed on-call**, so
this proposes a **business-hours** support posture, not a 24×7 SLA. Docs only;
no runtime change. Placeholders only — no secrets, no real tenant names.

## 1. Current operational reality (repo evidence)

- Single-node local Chroma; durability/isolation verified across reload and
  hash-verified restore (`test_durable_multitenant_persistence`), but **no HA**.
- Metrics are **per-process**; `/health` is the liveness probe; the local alert
  checker (`scripts/alert_check.py`), scheduled runner (`scripts/monitoring_runner.sh`),
  Prometheus/Grafana pack (`deploy/observability/`), and Slack/email notifier
  (`webapi/notifications.py`) exist but require operator wiring.
- **No evidence of a staffed 24/7 on-call rotation.** Do not promise one.

## 2. SLOs (measurable from existing signals)

| SLO | Definition | Source | Target (proposed, tune per deployment) |
| --- | --- | --- | --- |
| Availability | `/health` returns 200 | external/proxy uptime check + `KuradenTargetDown` rule | 99.0% business-hours (proposed) |
| Answer success | grounded/approved answers ÷ chat requests | `chat_answer_mode_total` | report-only baseline first; no hard target until measured |
| Provider error rate | `chat_provider_error_total` ÷ chat requests | metrics | < 2% warn / < 10% page |
| Abstain (fallback) rate | `chat_used_fallback_total` ÷ chat requests | metrics | monitored; not contractual (abstain is a safety feature) |
| Restore integrity | hash-verified restore succeeds | `restore.sh` + `test_deploy_ops` | 100% on tested archives |

SLOs are **report-first**: establish a baseline from real traffic (and the
Prompt039 validation pack) before committing to contractual targets.

## 3. SLA menu (proposed — not a 24/7 guarantee)

| Tier | Support hours | Response (sev-1) | Notes |
| --- | --- | --- | --- |
| Pilot / Standard | Business hours (JST, Mon–Fri) | next business day | single-node; manual recovery |
| Enhanced | Extended business hours | 4 business hours | requires staffing commitment not yet in repo |
| 24×7 | — | — | **Not offered yet** (needs HA + staffed on-call) |

State assumptions explicitly in any contract: single-node, customer-run
infrastructure, business-hours support, recovery via documented backup/restore.

## 4. Incident severity

- **SEV-1**: service down (`/health` failing) or confirmed cross-tenant exposure.
- **SEV-2**: degraded quality (sustained high error/fallback) or auth-rejection
  burst.
- **SEV-3**: single-user issue, cosmetic, or non-blocking.

## 5. Escalation path

1. Alert fires (Prometheus rule or `alert_check.py` CRITICAL → Slack/email).
2. On-call operator acknowledges within the SLA window; opens an incident note.
3. SEV-1 → follow `limited_beta_rollback_runbook.md` (containment, key
   rotation, restore, revert tag, re-smoke); SEV-2 → tune/triage using the
   monitoring pack; SEV-3 → backlog.
4. Escalate to engineering owner if not contained within the tier's response
   window. Record root cause + follow-ups.

## 6. Customer communication templates (placeholders only)

> Subject: [<PROGRAM>] Service incident — <SEV> — <UTC_TIMESTAMP>
>
> Hello <CUSTOMER_CONTACT>,
>
> We are investigating <SHORT_NEUTRAL_DESCRIPTION> affecting <SCOPE>. Current
> status: <INVESTIGATING/IDENTIFIED/MONITORING/RESOLVED>. Next update by
> <UTC_TIMESTAMP>. No action is required from you <OR: please <ACTION>>.
>
> — <TEAM>

> Subject: [<PROGRAM>] Resolved — <SEV> — <UTC_TIMESTAMP>
>
> The incident affecting <SCOPE> is resolved as of <UTC_TIMESTAMP>. Cause:
> <BRIEF_NON_SENSITIVE_CAUSE>. Follow-ups: <ITEMS>. A full post-incident review
> is available on request.

Never include secrets, raw keys, real tenant identifiers, or another customer's
information in any communication.

## 7. What not to claim

- No 24×7 SLA / HA / automatic failover (single-node; no staffed on-call).
- No availability/accuracy guarantee until a measured baseline exists.
- No compliance certification.

## Verification (docs-only)

No runtime change; no tests added. `pytest --collect-only` unchanged.

## Final judgment: PASS

## Next recommendation

Prompt053 — commercial production acceptance gate (recompute readiness).
