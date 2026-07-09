# KuraDen 蔵伝 — Competitive Positioning (Honest, Buyer-Facing)

This document is buyer-facing but deliberately conservative. Claims here are
backed by repository evidence; items still requiring real-environment validation
are marked. Competitor comparisons are **archetype comparisons, not fresh
market-share research** (external facts not revalidated; see repo market reports).

## Target customer

Japanese **mid-to-large manufacturers** that need internal technical-knowledge /
manual / procedure (SOP) / 規程 QA, where **data cannot leave the network** and
**wrong answers are unacceptable**. First engagement: **one department, on-prem,
PoC**.

## Positioning (one line)

**「閉域（オンプレ）で動く、誤答しない社内文書アシスタント。」** — data never leaves
your network, every answer is cited, and the system says "分かりません" instead of
guessing. Approved Q&A answers are deterministic.

## When to choose KuraDen

- You require **on-prem / closed-network** with no cloud dependency.
- You need **grounded, cited** answers from your own PDFs/Word/Excel/PPT/CSV.
- You value **no hallucination** (abstain-first) for SOP/safety/規程 content.
- You want **per-department tenant isolation**, **SSO/RBAC**, and an **operations
  pack** (monitoring/alerts/backup) out of the box.

## When NOT to choose KuraDen

- You want a **general-purpose AI assistant** (use ChatGPT/Claude Enterprise).
- You are **fully Microsoft-365-centric** and want Teams-native (Copilot fits).
- You need a **public customer-support web chatbot** (different category).
- You need **HA / 24×7 SLA / large-scale multi-tenant SaaS / compliance certs
  today** — not yet available.

## Comparison by product archetype

| Archetype | KuraDen stronger | KuraDen weaker | Don't compete on |
| --- | --- | --- | --- |
| ChatGPT/Claude Enterprise | on-prem, abstain-first, approved-Q&A, doc-cited | general reasoning, scale, polish | being a general assistant |
| MS Copilot / Copilot Studio | on-prem, vendor-neutral, deterministic answers | M365/Graph/Teams integration | deep M365 integration |
| Dify-style RAG builder | delivered, safety-first product + ops | flexibility, plugins, community | being a build-platform |
| Generic SaaS chatbot | internal, grounded, no-hallucination | public-web UX, CRM/ticketing | public support deflection |
| JP enterprise RAG product | true on-prem, abstain-first, small-vendor speed | track record, logos, scale | references/SLA today |
| On-prem/private RAG peer | integrated UI+SSO+RBAC+monitoring (tested) | HA, scale, certifications | multi-region HA |

## Safe claims (evidence-backed)

- On-prem / closed network, no cloud dependency.
- Multi-format ingestion with citations; abstain-first; deterministic approved
  answers.
- Tenant isolation verified across reload and hash-verified restore.
- Default-off layered auth: API key + reverse-proxy SSO bridge + in-app OIDC
  (Authorization Code + PKCE, JWKS-verified) + group→tenant RBAC; fail-closed;
  no secrets exposed.
- Commercial UI (workspace, citations panel, history, admin console, branding) +
  ingestion dry-run UI + operations pack (Prometheus/Grafana, scheduled checks,
  Slack/email alerts, backup/restore). 832 automated tests pass.

## Caveats (state these proactively)

- **Accuracy on real/manufacturing documents is measured during the PoC, not
  guaranteed in advance.**
- **SSO/OIDC and notifications are validated with mocks**; they are validated
  end-to-end against your IdP/endpoints during staging.
- **Single-node; business-hours support**; no HA/24×7/compliance certs yet.

## Common objections & honest responses

- *"Why not ChatGPT Enterprise?"* → Your data never leaves your network, answers
  cite your documents, and it abstains on weak evidence — for SOP/規程 that is the
  point.
- *"We're a Microsoft shop."* → OIDC connects to Entra ID (deployment guide
  included); run KuraDen for closed-network document QA alongside Copilot.
- *"We could build this on Dify."* → You would still need the guard, isolation,
  RBAC, monitoring, and runbooks we ship and test.
- *"You have no references/logos."* → Start with a low-risk on-prem PoC on
  synthetic data with **measured** results before any commitment.

## Suggested sales narrative (first PoC / annual)

1. Demo on synthetic manufacturing docs: a cited answer, an approved-Q&A exact
   answer, a too-general abstain, and a no-answer case (no hallucination).
2. Scope a **3-month on-prem PoC** for one department on the customer's
   sanitized documents; define KPIs (first-answer rate, abstain rate, error
   rate, citation quality,工数削減). Measure during the PoC.
3. Validate SSO against the customer's IdP in staging; wire monitoring/alerts.
4. On successful metrics, move to a **single-department annual contract** with a
   **business-hours** support model and documented backup/restore/rollback —
   explicitly **not** a 24×7/HA/SaaS commitment.

Pricing references and the Japanese market map are in
`docs/reports/japan_rag_competitor_price_web_research.md` and
`docs/reports/commercial_product_development_roadmap_after_prompt032.md`
(external freshness not revalidated here).
