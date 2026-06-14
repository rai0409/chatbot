# KuraDen 蔵伝 — Paid PoC Proposal & Scope of Work (TEMPLATE)

Non-lawyer draft. **All legal/commercial terms require review by qualified legal
and commercial experts before use.** Placeholders only; no real customer names or
secrets. Claims are conservative and evidence-based.

## 1. Proposal summary

A **3-month, on-premises, one-department** Proof of Concept of KuraDen — an
internal-document RAG assistant that answers in Japanese **with citations**,
**abstains instead of guessing** on weak evidence, and runs **on the customer's
closed network** (no cloud egress).

## 2. Scope of work

- Deploy KuraDen on a customer-provided on-prem host behind the customer's
  TLS/reverse proxy; serve via the `production_safe` profile.
- Ingest a **sanitized** subset of the customer's documents into an explicit
  **non-production** collection (dry-run validated; no production-collection
  mutation).
- Configure tenant + access (API key and/or reverse-proxy SSO / in-app OIDC).
- Run the **measured PoC evaluation** (see
  `docs/operations/real_document_poc_evaluation_workflow.md`) with human review.
- Wire monitoring/alerts in the customer environment
  (`docs/operations/monitoring_ops_acceptance_checklist.md`).

## 3. Success metrics (agreed before start)

- First-answer rate on answerable questions.
- Answer-correct + citation rate (human-judged).
- Correct-abstain rate on vague / out-of-corpus questions.
- **Error rate (wrong-but-confident)** — safety-critical; target ~0.
- 問い合わせ工数削減 (time saved), estimated.

Targets are **set jointly and measured during the PoC; not guaranteed in
advance.**

## 4. Pricing assumptions (DRAFT — not a quote)

- PoC engagement fee: `<assumption>` (fixed-scope, 3 months).
- Excludes: customer infrastructure, IdP/proxy setup labor on the customer side,
  document sanitization.
- Annual license (if PoC succeeds): proposed separately (see
  design-partner → annual report).

## 5. Customer responsibilities

- On-prem host + TLS/reverse proxy + (optional) IdP staging tenant.
- Provide **sanitized** documents (no unauthorized PII / third-party
  confidential content).
- Name a business owner + an operator for the PoC; staff human-in-the-loop
  review.

## 6. Data-handling caveats

- Data stays on the customer's network; KuraDen does not send documents to the
  cloud. The container carries no secrets (proven by the deploy smoke).
- The vendor does **not** receive or retain customer documents; PoC outputs stay
  on the customer host. Use an **alias** in any shared artifact.

## 7. Claim boundary (what we do / do not promise)

- **Do:** on-prem, cited answers, abstain-first, deterministic approved Q&A,
  tenant isolation, layered default-off auth, operations pack.
- **Do NOT promise:** accuracy guarantees on real documents (measured, not
  guaranteed), general production / HA / 24×7 SLA, compliance certification,
  multi-tenant SaaS at scale, or superiority over a named competitor.

## 8. Out of scope (PoC)

HA/failover, 24×7 support, compliance certification, large-scale rollout,
multi-department production — addressed (if pursued) in the annual phase.
