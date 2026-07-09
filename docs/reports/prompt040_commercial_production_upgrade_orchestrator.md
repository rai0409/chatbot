# Prompt040: Commercial Production Upgrade Orchestrator (UI / SSO / Ops)

Orchestration plan to upgrade 蔵伝 / KuraDen from PoC-level readiness to real
commercial chatbot delivery readiness across three pillars: **commercial-grade
UI**, **real SSO integration**, and **commercial operations/monitoring**.

This prompt produces the staged plan + the implementation prompt files
(041–053), commits/tags itself, then executes Prompt041 (the first stage). It
deliberately does **not** implement all three pillars in one diff.

- Branch: `eval/real-vector-evidence`; HEAD at planning time: `026b3d0`.
- No `.env` read; no secrets; no vectorstore mutation; no Docker/deploy/push.
- Orphans (`docs/reports/japan_rag_market_positioning_after_prompt030.md`,
  `prompts/claude/market/`) left untouched.

## Current state summary (from Prompt038 evidence)

- Core RAG/QA, multi-format ingestion, citations, approved-Q&A, abstain guard,
  tenant isolation (reload/restore-proven), API-key tenant authorization,
  default-off rate limiting, default-off enterprise-auth bridge, JSON+Prometheus
  metrics, local alert checker, deploy smoke/backup/restore, dry-run onboarding,
  minimal end-user `/chat-ui` — all **code+test verified** (761 tests collected).
- Readiness (Prompt038): internal demo READY; manufacturing 1-dept PoC and
  limited beta READY WITH CONDITIONS; first paid annual contract PARTIAL;
  general production NOT READY.
- Dependency baseline: `fastapi 0.128`, `starlette 0.50`, `pydantic 2`,
  `chromadb`, `openai`, `uvicorn`, `jinja2`, `tiktoken`, `rank-bm25`. **No OIDC
  / SSO / SAML / notification / Prometheus-client library present.**
- UI today: `webapi/static/chat.html` (263 lines, vanilla JS) calling
  `/chat/stream` + `/chat/feedback` with a runtime API-key field.

## Why the prompts are split this way

The three pillars are independent in delivery risk and review surface, and
several steps require a **decision before implementation** (notably SSO, where
a new dependency may be needed). Splitting keeps each diff reviewable, each
stage independently green, and lets a FAIL stop the program without polluting
later stages. UI is sequenced first because it unblocks demos/PoC value with the
lowest dependency risk (pure frontend over existing APIs); SSO second because it
is the gating enterprise requirement but needs a dependency decision; operations
third because it hardens the path to a paid annual contract.

## Exact dependency order

1. **UI series (041→042→043→044)** — 041 establishes the workspace shell that
   042 (history), 043 (admin/role/branding), 044 (ingestion UI) build on.
2. **SSO series (045→046→047→048)** — 045 (decision) MUST precede 046 (OIDC
   impl); 047 (group→tenant RBAC + audit) depends on 046's session identity;
   048 (deployment guides) depends on the implemented behavior.
3. **Operations series (049→050→051→052)** — 049 (Prometheus/Grafana) feeds 050
   (scheduled runner) and 051 (notifications); 052 (SLO/SLA + escalation)
   depends on the realized monitoring.
4. **Final gate (053)** — depends on all of the above; recomputes readiness.

Cross-pillar: 043 (admin/role UI) is strengthened by 047 (RBAC) but must work
default-off without SSO; 044 (ingestion UI) pairs with the later
safe-collection-promotion work referenced in Prompt038. Each prompt is
independently committable and preserves prior behavior unless in its scope.

## SSO path recommendation and rationale

**Primary in-app path: OIDC / OAuth2 Authorization Code + PKCE, default-off**,
implemented in 046 only after the 045 decision confirms fit. Rationale:

- OIDC is the modern lingua franca for Entra ID, Okta, Google, Keycloak, Auth0
  — one in-app path covers most enterprise IdPs.
- It composes cleanly with the existing tenant-authorization model: the OIDC
  session yields an identity + groups that map to `allowed_tenants` (reusing the
  Prompt037 mapping approach), never broadening tenant access.
- **No homegrown crypto.** Token/JWKS verification requires a mature library;
  `authlib` (widely used, FastAPI/Starlette-compatible) is the candidate, added
  in 046 **only if 045 justifies it** and pinned minimally. SAML/LDAP are
  treated as **gateway/reverse-proxy** integrations (the existing Prompt037
  trusted-header bridge already covers the proxy path) rather than in-app.

**What "SSO-connected" means for this product:** a default-off OIDC login that
establishes a server-side session, maps IdP identity/groups to tenant + role,
and feeds the existing authorization/isolation. **Locally implementable+testable:**
the full code path with a mock IdP / mock JWKS and synthetic tokens. **Requires
a real customer IdP tenant:** end-to-end validation against Entra ID/Okta (client
registration, real JWKS, real group claims) — documented in 048, not asserted
as verified.

## UI target architecture

A ChatGPT/Claude-style workspace served by the existing backend (no SPA build
step unless 041 justifies one; prefer vanilla/ESM to honor "no new
dependencies" by default):

- **Shell**: sidebar (conversations) + main chat workspace + right-hand
  **citations panel**; calm **abstain/no-answer** state; feedback UX.
- **Branding**: customer logo/text/theme via a safe server-provided config
  (no secrets).
- **Role-aware UI** (admin/operator/user/viewer) with **backend-enforced**
  gating (frontend gating is cosmetic only).
- **Admin console**: review queue, ingestion UI, document/job status,
  user/tenant awareness.
- **Safety**: no raw API key/SSO secret in page source; runtime credential
  entry only; defensive escaping; generic error text.

## Operations target architecture

Upgrade the local `alert_check.py` into a commercial monitoring pack:

- **Prometheus** scrape config + **Grafana** dashboard JSON over the existing
  safe aggregate counters (049).
- **systemd service/timer + cron fallback** running health/metrics/alert checks
  with log rotation + snapshot retention (050).
- **Slack webhook + SMTP email** notifications with severity routing,
  retry/backoff, env-only secrets, mock-only tests (051).
- **SLO/SLA + incident escalation runbook** + customer comms templates, honest
  about current single-node/no-24x7 reality (052).
- Hard rule across all: **no tenant data, prompts, document text, API keys, or
  trust tokens in metrics/alerts/dashboards**.

## Risks and stop conditions

- **Scope/size**: 13 substantive prompts; doing all in one session risks
  breakage. **Stop rule**: execute sequentially; on any FAIL/PARTIAL stop and
  write a blocker/resume report; do not proceed to the next stage.
- **New dependency (OIDC)**: gated behind the 045 decision; if not justified,
  fall back to the proxy/gateway path (Prompt037) and document it.
- **Behavior preservation**: every prompt runs targeted tests first and must not
  weaken auth/tenant/isolation/rate-limit/production_safe; full suite run only
  when reasonable and explicitly reported.
- **Secrets**: env-only; never in UI/logs/metrics/alerts/reports/tests.

## Commercial claim boundary — before vs after this upgrade

**Before (today):** on-prem/no-cloud, multi-format + citations, abstain-first,
approved-Q&A, tenant isolation (reload/restore), API-key tenant auth + default-off
enterprise-auth bridge, backup/restore + deploy smoke + local alert checker,
minimal end-user UI. NOT claimable: accuracy numbers on real docs, general
production/HA/SLA, multi-tenant SaaS, full in-app SSO, always-on monitoring.

**After this upgrade program (target, when 041–053 all PASS):** commercial-grade
UI with admin console + branding + ingestion UI; default-off OIDC SSO with
group→tenant RBAC + audit (locally tested; customer-IdP validation documented);
Prometheus/Grafana + scheduled checks + Slack/email alerts + SLO/SLA runbook.
Still NOT claimable without separate evidence: HA/24×7 SLA, compliance
certifications, large-scale multi-tenant SaaS, real-IdP end-to-end without a
customer tenant, accuracy guarantees on real documents (covered by Prompt039).

## Prompts generated

`prompt041_commercial_chat_workspace_ui.md`,
`prompt042_conversation_history_and_thread_persistence.md`,
`prompt043_admin_console_role_based_branding_ui.md`,
`prompt044_document_ingestion_ui_and_job_status.md`,
`prompt045_enterprise_sso_architecture_decision.md`,
`prompt046_oidc_login_session_integration.md`,
`prompt047_group_tenant_rbac_mapping_and_audit.md`,
`prompt048_entra_okta_reverse_proxy_tls_deployment_guides.md`,
`prompt049_prometheus_grafana_observability_pack.md`,
`prompt050_systemd_cron_monitoring_runner.md`,
`prompt051_slack_email_alert_notifications.md`,
`prompt052_slo_sla_incident_escalation_runbook.md`,
`prompt053_commercial_production_acceptance_gate.md`
(all under `prompts/claude/product/`, no triple-backtick fences, self-checked).

## Prompt041 execution

**Started** in this session after committing/tagging Prompt040 — Prompt041 scope
(commercial chat workspace UI over existing APIs) is clear, low-dependency, and
self-contained. Per the stop rule and to avoid a giant unreviewable diff, the
program then pauses with a resume report naming the next exact prompt
(`prompt042_conversation_history_and_thread_persistence.md`); subsequent stages
run in later sessions, each independently committed/tagged on PASS.
