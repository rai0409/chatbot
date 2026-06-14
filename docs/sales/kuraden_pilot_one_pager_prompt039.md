# KuraDen 蔵伝 — Manufacturing Pilot One-Pager (Honest)

Buyer-facing, conservative. Claims are backed by repo evidence; items needing
real-environment validation are marked.

## What it is

An **on-prem / closed-network** internal-document RAG assistant for Japanese
manufacturers: ingest your PDFs/Word/Excel/PPT/CSV, ask in Japanese, get
**cited** answers — and **「分かりません」 instead of a guess** when the evidence is
weak. Reviewed Q&A are answered exactly.

## Why a manufacturer cares

- **Data never leaves your network** (no cloud dependency).
- **Never wrong by guessing**: abstain-first on weak evidence — for 手順書・規程・
  安全 that is the point.
- **Excel/PowerPoint/PDF** internal documents, with **source citations**.
- **Per-department isolation**, SSO (reverse-proxy or OIDC), role-based access,
  and an operations pack (monitoring, alerts, backup/restore).

## Measured pilot evidence (synthetic manufacturing corpus)

On a synthetic 架空精機 / 装置X corpus, a repeatable evaluation showed
**11/11** designed behaviors: 8/8 grounded questions (procedure / quality /
safety / troubleshooting / IT-FAQ + an approved-Q&A exact match) returned the
**correct source with a citation**; 2/2 vague questions and 1/1 out-of-corpus
question **abstained / returned no-answer** instead of fabricating.
(See `docs/reports/prompt039_pilot_validation_and_demo_readiness_pack.md`.)

## Honest caveats (we say these up front)

- **Accuracy on your real documents is measured during the PoC, not guaranteed
  in advance.** The numbers above are on synthetic data and measure retrieval +
  abstain behavior, not real generated-answer wording.
- **Single-node, business-hours support** today — no HA / 24×7 SLA / compliance
  certifications.
- SSO/OIDC and alerting are validated against your real IdP/endpoints during
  staging (in-repo tests use mocks).

## Suggested first engagement

A **3-month, on-prem, one-department PoC** on your **sanitized** documents:
deploy behind your TLS/proxy, ingest via a reviewed dry-run into a
non-production collection, serve via the safe profile with human-in-the-loop
review, and **measure** first-answer rate, abstain rate, error rate, citation
quality, and問い合わせ工数削減. On good results, move to a single-department
annual contract with a business-hours support model.

## What we will not promise

General production / HA / 24×7 SLA, compliance certifications, multi-tenant SaaS
at scale, accuracy guarantees on real documents, or superiority over a named
competitor.
