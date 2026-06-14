# Prompt054: Commercial Usage Flow & Competitive Readiness Analysis

Analysis/report only. Repo-evidence-based; no product runtime change; no `.env`
read; no secrets; no vectorstore mutation; no Docker/deploy/push. Orphans
(`docs/reports/japan_rag_market_positioning_after_prompt030.md`,
`prompts/claude/market/`) left untouched.

- HEAD: `2220ebe` (`prompt053 commercial production acceptance gate`).
- Test collection: **832 tests collected** (`pytest --collect-only`). The full
  suite was **not run in this analysis prompt**; per-stage reports record full
  suite green at their commits (Prompt053 recorded 832 passed).
- Evidence labels: **implemented** (code+test), **mock-tested only**,
  **documented only**, **missing**, **unclear**.
- Competitive comparison (Section 4) is an **archetype comparison, not fresh
  market-share research**; competitor pricing/share facts come only from repo
  market reports and external freshness is **not revalidated** in this prompt.

---

## Section 1 — Executive judgment

| Gate | Label |
| --- | --- |
| Internal demo | **READY** |
| Manufacturing one-department PoC | **READY WITH CONDITIONS** |
| Limited external beta | **READY WITH CONDITIONS** |
| First paid annual contract | **READY WITH CONDITIONS** |
| General production | **NOT READY** |

- **Can it be sold now?** Yes — as a **limited, on-prem, single-department PoC**
  to a **Japanese mid-to-large manufacturer**, under conditions.
- **To whom:** a design-partner manufacturer's DX/情シス/技術部門 willing to run
  on-prem with synthetic/sanitized documents first.
- **Under what conditions:** `production_safe` profile; on-prem/closed network +
  TLS; API-auth + (reverse-proxy SSO **or** default-off in-app OIDC) enabled;
  human-in-the-loop review; backup before launch; monitoring wired; and the
  pilot scope limited until manufacturing-domain accuracy is measured.
- **Must still NOT be promised:** measured accuracy on real documents, general
  production / HA / 24×7 SLA, compliance certification, large-scale multi-tenant
  SaaS, or end-to-end SSO verified against the customer's real IdP (app-side
  logic is mock-tested only).

---

## Section 2 — What is implemented now

Legend: implemented = code+test; mock-tested only = tested with synthetic mocks;
documented only = docs without runtime; missing/unclear as labelled.

| # | Category | Status | Evidence (code) | Test evidence | Commercial significance | Limitation |
| --- | --- | --- | --- | --- | --- | --- |
| A | Core RAG & QA (retrieve, citations, approved-Q&A, abstain) | implemented | `rag_core/qa.py`, `retrieval.py`, `approved_qa.py`, `source_metadata.py` | `test_confidence_guard`, `test_too_general_guard_redesign`, `test_no_answer_citations`, `test_approved_qa`; smoke eval 21/21, qa_pair 7/7 | Core value (never-wrong) | Accuracy on real manufacturing docs **unmeasured** |
| B | Document ingestion + job status | implemented (dry-run) | `webapi/ingestion_jobs.py`, `scripts/import_manifest.py`, `onboard_documents_dry_run.py`, `webapi/static/ingestion.html` | `test_ingestion_ui_job_status`, `test_multiformat_onboarding` | Pilot intake safety | Dry-run validation only; prod-collection ingest refused (by design) |
| C | Chat workspace UI | implemented | `webapi/static/chat.html`, `GET /chat-ui` (`webapi/main.py`) | `test_commercial_chat_workspace_ui`, `test_enduser_chat_ui` | Demo/PoC usability | No SSO login button in-page; runtime key field for API-auth |
| D | Conversation history / threads | implemented (default-off) | `webapi/conversation_store.py`, `/chat/threads*` | `test_conversation_history` | Usability | Default-off; per-process local store |
| E | Citations / abstain / feedback | implemented | `qa.py`, `source_metadata.py`, `/chat/feedback`, `chat_feedback_total` | `test_no_answer_citations`, `test_monitoring_alerts` | Trust + signal | Feedback by client UUID token |
| F | Admin console / branding / role-aware UI | implemented | `webapi/branding.py`, `/branding`, `/ui/context`, `/admin/review*` | `test_admin_console_roles_branding`, `test_admin_auth` | Enterprise feel | Admin console = review queue + ingestion; backend-enforced |
| G | Tenant isolation + API-key authorization | implemented | `webapi/api_auth.py`, `rag_core/retrieval.py` | `test_tenant_isolation`, `test_api_key_tenant_authorization`, `test_durable_multitenant_persistence` | Multi-dept safety | Single-node; isolation by metadata filter |
| H | OIDC SSO + reverse-proxy enterprise auth | **mock-tested only** (OIDC) / implemented (bridge) | `webapi/oidc_auth.py` (authlib/cryptography JWKS), `enterprise_auth.py` | `test_oidc_login_session` (synthetic RSA/JWKS), `test_enterprise_auth_bridge` | Enterprise gating | **End-to-end vs a real IdP not verified** (needs customer tenant) |
| I | Group→tenant mapping + RBAC | implemented (mock identity) | `webapi/rbac.py`, `ApiAuthContext.role` | `test_group_tenant_rbac` | Dept/role control | Roles derived from mock/claims; real group claims need real IdP |
| J | Monitoring + Prometheus/Grafana + alerts | implemented | `webapi/metrics_registry.py`, `alerting.py`, `deploy/observability/*` | `test_observability_pack`, `test_monitoring_alerts`, `test_observability_export` | Operability | Per-process metrics; operator must wire scrape |
| K | systemd/cron monitoring runner | implemented | `scripts/monitoring_runner.sh`, `deploy/monitoring/*` | `test_monitoring_runner` | Unattended ops | Operator installs units; local-only |
| L | Slack/email notifications | implemented (mock transports) | `webapi/notifications.py`, `scripts/alert_notify.py` | `test_alert_notifications` | On-call signal | Real send needs env config; mock-tested only |
| M | SLO/SLA + incident runbooks | documented only | `docs/reports/prompt052_*`, `docs/reports/limited_beta_rollback_runbook.md` | n/a | Contract discussions | No 24×7 staffing evidence |
| N | Backup/restore/rollback/deploy | implemented | `scripts/backup.sh`, `restore.sh`, `deploy_smoke.sh`, `docs/operations.md` | `test_deploy_ops` | On-prem ops baseline | Operator-run; not automated/CI |
| O | Evaluation + readiness gates | implemented | `eval/production_readiness_report.py`, `scripts/product_readiness_smoke.sh`, `limited_beta_preflight.sh` | `test_production_readiness_report` | Launch governance | Static + synthetic; decision `needs_review` |

---

## Section 3 — Actual usage flow (end to end)

Roles: **Vendor** (KuraDen team), **Customer IT**, **Admin/Operator**, **End user**.
Readiness tag per step: D=demo, P=PoC, A=annual, N=not-ready.

1. **Pre-sales / demo** — *Vendor*. Entry: `GET /chat-ui` on a local instance with
   synthetic docs; demo script (to be produced by Prompt039). Result: live
   citations + abstain + approved-answer demo. Evidence: `chat.html`,
   `test_commercial_chat_workspace_ui`. Limitation: no manufacturing demo pack yet.
   **[D]**
2. **Tenant/customer setup** — *Vendor+Customer IT*. Entry: `API_AUTH_TENANT_MAP`
   / `configs/product_tenants/`. Result: tenant ids + allowed access. Evidence:
   `api_auth.py`, `test_api_key_tenant_authorization`. **[P]**
3. **SSO setup** — *Customer IT*. Entry: reverse-proxy bridge
   (`ENTERPRISE_AUTH_*`) **or** in-app OIDC (`ENTERPRISE_OIDC_*`); guide
   `docs/reports/prompt048_*`. Result: IdP-authenticated sessions → tenant/role.
   Evidence: `enterprise_auth.py`, `oidc_auth.py`. Limitation/risk: **end-to-end
   against the real IdP unverified (mock-tested only)**. **[A, with real-IdP test]**
4. **Admin/operator setup** — *Admin*. Entry: `ADMIN_AUTH_ENABLED` + token;
   `/admin/review`, `/admin/ingestion`. Result: privileged console access
   (backend-enforced). Evidence: `admin_auth.py`, `test_admin_console_roles_branding`.
   **[P]**
5. **Branding** — *Admin/Vendor*. Entry: `BRANDING_*` env → `/branding`. Result:
   product name/subtitle/theme. Evidence: `branding.py`. **[D]**
6. **Document ingestion (validation)** — *Operator*. Entry: `/admin/ingestion`
   or `scripts/onboard_documents_dry_run.py` → dry-run import manifest into a
   **non-production** collection. Result: duplicate/tenant/collision report +
   job status. Evidence: `ingestion_jobs.py`, `test_ingestion_ui_job_status`.
   Limitation: **safe promotion to a served collection is not yet a built
   workflow**. **[P for validation; N for guarded promotion]**
7. **Staging / safe validation** — *Operator*. Entry: `persistence_isolation_check.sh`,
   `product_readiness_smoke.sh`, `limited_beta_preflight.sh`. Result: green gates.
   Evidence: `test_durable_multitenant_persistence`. **[P]**
8. **End-user chat** — *End user*. Entry: `/chat-ui` → `/chat/stream`. Result:
   streamed, cited answer or calm abstain. Evidence: `chat.html`, `qa.py`,
   `test_chat_stream`. **[P]**
9. **Conversation history** — *End user*. Entry: default-off `/chat/threads*`.
   Result: per-user/tenant-isolated history. Evidence: `conversation_store.py`,
   `test_conversation_history`. **[P when enabled]**
10. **Feedback / human review** — *End user→Operator*. Entry: feedback buttons →
    `/chat/feedback`; `/admin/review`. Result: signal + review queue. Evidence:
    `test_review_*`, `chat_feedback_total`. **[P]**
11. **Monitoring/alert** — *Operator*. Entry: `/metrics?format=prometheus`,
    `deploy/observability/*`, `scripts/alert_check.py`. Result: dashboards +
    thresholds. Evidence: `test_observability_pack`, `test_monitoring_alerts`.
    **[A once wired]**
12. **Incident response** — *Operator*. Entry: alert → `prompt052` runbook +
    `limited_beta_rollback_runbook.md`. Result: containment/rollback. **[A; no
    24×7]**
13. **Backup/restore/rollback** — *Operator*. Entry: `backup.sh`/`restore.sh`
    (hash-verified) + revert tag. Evidence: `test_deploy_ops`. **[P/A]**
14. **PoC measurement** — *Vendor+Operator*. Entry: `eval.runner` + (pending)
    manufacturing pack. Result: first-answer/abstain/error/citation metrics.
    Limitation: **manufacturing validation pack not yet executed (Prompt039)**.
    **[N until Prompt039]**
15. **PoC → annual** — *Vendor*. Entry: measured PoC + SLO/SLA runbook + real-IdP
    SSO test. Result: contract. **[A once PoC measured + real-IdP test]**

---

## Section 4 — Commercial product-level flow comparison

**Archetype comparison, not fresh market-share research.** Repo market context:
`docs/reports/japan_rag_competitor_price_web_research.md` (Prompt032, web-verified
at that date; external freshness **not revalidated** here),
`commercial_repo_competitor_analysis.md`, `commercial_product_development_roadmap_after_prompt032.md`.

### vs ChatGPT Enterprise / Claude Enterprise (general-purpose AI assistant)
- **Stronger:** true on-prem/closed-network, abstain-first + approved-Q&A
  determinism, per-tenant isolation, citations grounded in the customer's docs.
- **Weaker:** general reasoning breadth, model quality/scale, polish, ecosystem.
- **Don't compete on:** being a general AI assistant.
- **Best-fit:** data-can't-leave manufacturers needing grounded internal QA.
  **Worst-fit:** teams wanting a broad general copilot.
- **Gaps:** model breadth, mobile apps, large ecosystem.
- **Objection:** "Why not ChatGPT Enterprise?" → **Response:** data never leaves
  your network, answers cite your documents, and it says "分かりません" instead of
  guessing — for SOP/規程 that matters more than general chat.

### vs Microsoft Copilot / Copilot Studio (M365 ecosystem)
- **Stronger:** on-prem/no-cloud-dependency, vendor-neutral, deterministic
  approved answers. **Weaker:** M365/Graph integration, Teams-native UX, scale.
- **Don't compete on:** deep M365 integration.
- **Best-fit:** non-M365-centric or cloud-restricted shops. **Worst-fit:** fully
  M365-committed orgs wanting Teams-native.
- **Objection:** "We're a Microsoft shop." → **Response:** OIDC connects to Entra
  ID (Prompt048 guide); use KuraDen for closed-network document QA alongside
  Copilot.

### vs Dify-style RAG/app builder
- **Stronger:** opinionated, safety-first product (abstain + approved-Q&A +
  tenant isolation + ops pack) rather than a build-it-yourself toolkit.
  **Weaker:** flexibility, plugin breadth, community.
- **Best-fit:** buyers wanting a delivered product, not a platform. **Worst-fit:**
  teams wanting to build their own flows.
- **Objection:** "We could build this on Dify." → **Response:** you'd still need
  the guard, isolation, RBAC, monitoring, and runbooks we ship and test.

### vs Generic SaaS website/support chatbot
- **Stronger:** internal/on-prem, document-grounded, no hallucination posture.
  **Weaker:** public-web widget UX, CRM/ticketing integrations.
- **Best-fit:** internal employee QA. **Worst-fit:** public customer-support
  deflection.
- **Don't compete on:** public-facing support automation.

### vs Enterprise internal knowledge search / RAG product (e.g. JP players)
- **Stronger:** true on-prem option, abstain-first, small-vendor speed/price.
  **Weaker:** track record, logos, scale, managed cloud. (Repo:
  `japan_rag_competitor_price_web_research.md` — those vendors are largely
  quote-based; **not revalidated**.)
- **Best-fit:** closed-network manufacturer pilot. **Worst-fit:** buyers needing
  references/SLA now.
- **Objection:** "You have no logos." → **Response:** offer a low-risk on-prem PoC
  on synthetic data with measured results before commitment.

### vs On-prem/private RAG solution (peer category)
- **Stronger:** integrated UI+SSO+RBAC+monitoring+runbooks, all tested.
  **Weaker:** HA, large-scale, certifications.
- **Best-fit:** single-site, single-department first. **Worst-fit:** multi-region
  HA requirement.

---

## Section 5 — Is it commercially usable now?

- **Used internally now?** **Yes.** Run locally; all suites green; synthetic data.
- **Demoed to a customer now?** **Yes, with conditions** — use synthetic docs and
  the `production_safe` route; a polished manufacturing demo script is pending
  (Prompt039). Caveat: do not present accuracy numbers.
- **Used in a paid PoC now?** **With conditions** — on-prem, single department,
  synthetic/sanitized docs, human-in-the-loop, measured KPIs. **Blocker:**
  manufacturing accuracy/abstain not yet measured (Prompt039). Contract wording:
  "PoC on customer-provided sanitized documents; accuracy measured during the
  PoC, not guaranteed in advance."
- **First paid annual contract now?** **With conditions** — additionally requires
  end-to-end SSO validated against the customer's IdP, monitoring/alerting wired,
  an agreed business-hours support model, and successful PoC metrics. Wording:
  "single-node, business-hours support; on-prem customer-operated; no 24×7/HA."
- **General production SaaS now?** **No.** No HA/failover, no 24×7 on-call, no
  compliance certification, no large-scale multi-tenant SaaS.

Operational assumptions throughout: customer-operated on-prem host, TLS at the
proxy, operator available business hours. Legal/compliance: no certification
claims; data-handling per the customer agreement (synthetic-first).

---

## Section 6 — Remaining blockers (prioritized)

**P0 — before a paid PoC**
- Measured manufacturing-domain accuracy/abstain/error/citation results
  (Prompt039 pack) — *currently unmeasured*.
- A reproducible synthetic manufacturing demo/validation corpus (Prompt039).

**P1 — before first annual contract**
- Real customer-IdP end-to-end SSO test (OIDC is **mock-tested only**).
- Safe production/default collection **promotion** workflow (dry-run exists;
  guarded promotion does not).
- Monitoring/alerting **wired** at the customer (pack exists; install required).
- Real-customer document evaluation workflow (synthetic-first procedure).
- Agreed support model / on-call staffing (runbook exists; staffing not in repo).

**P2 — before broader production**
- HA / failover (single-node today).
- 24×7 support, compliance/security review, audit-log **export**, data-retention
  policy enforcement automation.
- Large-scale multi-tenant SaaS readiness.

**P3 — future scale/quality**
- Cross-encoder/reranker **promotion** (parked; model not cached).
- pgvector/Qdrant vectorstore **adapter/migration** (not started; single-node
  Chroma today).

---

## Section 7 — What to do next

- **Immediate next prompt: `prompt039_pilot_validation_and_demo_readiness_pack.md`**
  (already generated). It is still the **best next step**: every "READY WITH
  CONDITIONS" gate is bottlenecked by the single unmeasured unknown —
  manufacturing-domain accuracy/abstain — and by the lack of a reproducible demo.
  It is low-risk (synthetic data, reuses ingestion/eval/UI) and unblocks PoC
  claims. **Execute Prompt039 next.**
- **Next 3 prompts (after 039):** (1) safe collection promotion workflow;
  (2) real-document evaluation workflow (synthetic-first); (3) real-IdP SSO
  validation checklist + (where possible) an integration test harness.
- **Next 30 days:** run Prompt039; assemble a pilot one-pager + demo script; line
  up one design-partner manufacturer for an on-prem PoC on synthetic docs.
- **Next 90 days:** complete a measured PoC; validate SSO against the partner's
  IdP; wire monitoring/alerts in their environment; agree a business-hours SLA;
  build the guarded collection-promotion path.
- **Sell first:** single-department on-prem manufacturing PoC (`production_safe`).
- **Do not build yet:** HA cluster, 24×7, multi-tenant SaaS billing, pgvector
  migration, cross-encoder promotion — premature before a measured PoC.

**Decision: do not generate a new prompt; execute the existing Prompt039 next.**

---

## Section 8 — Customer-facing claim boundary (strict)

**A. Safe to claim now**
- Runs fully **on-premises / closed network, no cloud dependency**.
- Ingests **PDF/DOCX/XLSX/CSV/PPTX** and answers **with citations**.
- **Abstains ("分かりません")** instead of hallucinating when evidence is weak;
  **deterministic approved-Q&A** exact-match answers.
- **Tenant isolation** verified across reload and hash-verified restore.
- Layered **default-off** auth: API key + reverse-proxy SSO bridge + in-app OIDC
  (Authorization Code + PKCE, JWKS-verified) + **group→tenant RBAC**, all
  fail-closed; no secrets exposed.
- **Commercial-grade UI** (workspace, citations panel, history, admin console,
  branding) + **ingestion dry-run UI**.
- **Operational pack**: Prometheus/Grafana, scheduled monitoring runner,
  Slack/email alerts, backup/restore, deploy smoke, honest SLO/SLA runbook.
- **832 automated tests** pass at HEAD (per Prompt053).

**B. Not safe to claim yet**
- Any **accuracy/quality number** on real or manufacturing documents.
- **End-to-end SSO verified against a real Entra/Okta tenant** (mock-tested only).
- **General production / HA / 24×7 SLA / failover.**
- **Compliance certifications** (ISO/SOC2/etc.).
- **Large-scale multi-tenant SaaS.**
- **Superiority over a named competitor** (no benchmark performed).
- "Notifications/alerting proven in production" (mock-tested only; needs wiring).

---

## Section 9 — Evidence appendix

- **Commits/tags checked:** HEAD `2220ebe`; tags `prompt038`…`prompt053`
  (15 program tags present).
- **Reports checked:** `docs/reports/prompt038_*`, `prompt040_*`…`prompt053_*`;
  market reports `commercial_repo_competitor_analysis.md`,
  `japan_rag_competitor_price_web_research.md`,
  `commercial_product_development_roadmap_after_prompt032.md`.
- **Test files checked:** `test_oidc_login_session`, `test_group_tenant_rbac`,
  `test_enterprise_auth_bridge`, `test_conversation_history`,
  `test_ingestion_ui_job_status`, `test_admin_console_roles_branding`,
  `test_commercial_chat_workspace_ui`, `test_observability_pack`,
  `test_monitoring_runner`, `test_alert_notifications`,
  `test_durable_multitenant_persistence`, `test_tenant_isolation`,
  `test_api_key_tenant_authorization`, `test_monitoring_alerts`.
- **Docs checked:** `docs/operations.md`, `prompt048_*` (SSO guides),
  `prompt052_*` (SLO/SLA), `limited_beta_*` runbooks.
- **Scripts/deploy checked:** `deploy/observability/*`, `deploy/monitoring/*`,
  `scripts/{monitoring_runner,alert_check,alert_notify,backup,restore,deploy_smoke,
  limited_beta_preflight,onboard_documents_dry_run,import_manifest,persistence_isolation_check}`.
- **Checks run this prompt:** `git log/tag/status`, file-existence checks,
  targeted greps, `pytest --collect-only` (**832 collected**).
- **Not run this prompt:** full `pytest` suite, Docker, evals, live server — to
  keep this analysis lightweight and non-mutating. Per-stage reports record the
  full suite green at their commits.

## Final judgment: PASS
