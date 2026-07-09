# Prompt055: Commercial Deployment Prompt Split & Save

Planning/prompt-generation only. No implementation; no product runtime change;
no `.env`/secrets; no vectorstore mutation; no Docker/deploy/push. Orphans
(`docs/reports/japan_rag_market_positioning_after_prompt030.md`,
`prompts/claude/market/`) left untouched.

- HEAD: `3f480f4` (`prompt039 pilot validation and demo readiness pack`).
- Generated **15 implementation-ready prompt files** (056–070), a JSON plan, and
  this report. `pytest --collect-only` → **839 collected**; full suite **not run
  this prompt** (planning only).

## 1. Executive judgment

KuraDen is a **PoC-ready commercial candidate** (internal demo READY; PoC /
limited beta / first annual all READY WITH CONDITIONS; general production NOT
READY — per Prompt053/054). The remaining work to reach a **real paid
deployment** is operational and evidence-gathering, not core product invention.
It splits cleanly into **P0** (enable a paid PoC), **P1** (enable a first annual
contract), **P2** (broader production hardening analysis), and a **final gate**.
All 15 prompts are self-contained, conservative, and committed/tagged
individually on PASS.

## 2. Current commercial state

- Implemented + tested: core RAG/QA, abstain-first guard, approved-Q&A,
  citations, tenant isolation (reload/restore-verified), API-key auth, OIDC
  (mock-tested) + group→tenant RBAC, commercial UI + admin console + branding +
  ingestion dry-run UI, conversation history, observability (Prometheus/Grafana
  + local checker + scheduled runner + Slack/email, mock-tested), backup/restore,
  SLO/SLA runbook, and a measured synthetic manufacturing pilot pack (Prompt039,
  11/11).
- Open conditions: accuracy on real documents (PoC-measured), real-IdP SSO,
  safe collection promotion, monitoring wired at the customer, support staffing,
  and general-production items (HA/compliance/scale).

## 3. Why the split (P0 / P1 / P2 / final gate)

- **P0** items are prerequisites to *start* a paid PoC (promotion safety, real-
  doc eval workflow, real-IdP validation steps, monitoring acceptance, sales/
  contract pack). Each is independently shippable and mostly auto-executable on
  synthetic data; two require a real customer environment to *complete*.
- **P1** items enable converting a PoC into a *first annual contract* (install/
  upgrade packaging, security/compliance evidence, DR drill, support ops, the
  PoC→annual decision report).
- **P2** items are *analysis/decision spikes* for broader production (HA,
  vectorstore backend, cross-encoder, readiness reassessment) — deliberately not
  built prematurely.
- **Final gate** recomputes readiness and decides go/no-go.

## 4. Generated prompt list & purpose

P0: 056 safe collection promotion · 057 real-document PoC eval workflow · 058
real-IdP SSO e2e validation · 059 customer monitoring wiring + ops acceptance ·
060 paid pilot sales/contract/onboarding pack.
P1: 061 on-prem install/upgrade/release packaging · 062 security/audit-export/
retention/compliance pack · 063 backup/restore DR drill + RPO/RTO · 064 support
staffing + incident ops · 065 design-partner PoC→annual report.
P2: 066 HA/failover/capacity spike · 067 vectorstore backend decision (pgvector/
Qdrant) · 068 cross-encoder rerank promotion decision · 069 general-production
gap reassessment.
Final: 070 commercial deployment acceptance gate.

## 5. Recommended execution order

Sequential by number: **056 → 057 → 058 → 059 → 060** (P0) → **061 → 062 → 063 →
064 → 065** (P1) → **066 → 067 → 068 → 069** (P2) → **070** (gate). 069 depends on
056–068; 070 depends on 069. 058 and 065 can be *started* sequentially but only
*completed* with a real customer environment.

## 6. Which prompts can run sequentially without human confirmation

All 15 are written to "proceed autonomously / no yes-no confirmation" and are
auto-executable on synthetic/local data. **056, 057, 059, 060, 061, 062, 063,
064, 066, 067, 068, 069, 070** can fully complete autonomously. **058** and
**065** auto-execute their synthetic/template portions but have a clearly-marked
**real-environment** portion that cannot be asserted as verified without the
customer's IdP / PoC results.

## 7. Which prompts require real customer/environment evidence

- **058 (real-IdP SSO e2e):** requires the customer's Entra ID/Okta tenant to
  validate end to end; in-repo work stays mock-tested + documented steps.
- **065 (PoC→annual report):** requires real measured PoC results to fill the
  templates (must not be fabricated).
- (057 and 060 reference real documents/customers but commit only templates;
  customer data stays gitignored.)

## 8. What must still not be claimed

Accuracy on real/manufacturing documents (PoC-measured, not guaranteed);
end-to-end SSO verified against a real IdP until 058 is completed in the
customer tenant; general production / HA / 24×7 SLA / compliance certification;
large-scale multi-tenant SaaS; competitor superiority; "monitoring/alerting
proven in production" (mock-tested until 059 wired at the customer).

## 9. Next immediate prompt to run

**`prompts/claude/product/prompt056_safe_collection_promotion_workflow.md`** — it
is the first P0 prerequisite (safe staging→served promotion + rollback), fully
auto-executable on synthetic data, and unblocks a real-document PoC go-live.

## 10. Verification performed

`git log/tag/status`; confirmed Prompt039/054 reports and key Prompt040–053
reports exist; generated 15 prompt files and confirmed **0 triple-backtick
fences** and that each contains the safety-constraints block; `pytest
--collect-only` → 839 collected. Full suite **not run** (planning prompt). No
fabricated results.

## Final judgment: PASS
