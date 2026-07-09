# KuraDen PoC → Annual Contract Decision Package (TEMPLATE)

> **Fill every `<...>` with REAL measured data from the design-partner PoC.
> Do NOT fabricate numbers, quotes, or outcomes.** Leave a field as
> `NOT MEASURED` rather than inventing it. No secrets, no real document text, no
> real identity data in this document.

## 1. Engagement summary

- Design partner (alias): `<ALIAS>`
- PoC window (UTC): `<START>` – `<END>`
- Scope: `<departments / corpora>`  · Users: `<count>`
- Deployment: on-prem single-node (no HA), business-hours support

## 2. Measured results (from `runs/poc/<alias>/`, gitignored)

| Metric | Result | Source | Notes |
| --- | --- | --- | --- |
| Answerable accuracy (graded) | `<x/N>` | PoC eval | grading method `<...>` |
| Citation-correct rate | `<x/N>` | PoC eval | |
| Approved-Q&A exact match | `<x/N>` | PoC eval | |
| Correct abstention (out-of-corpus) | `<x/N>` | PoC eval | abstain-first |
| p50 / p95 latency | `<ms>` | metrics | single-node |
| Error / fallback rate | `<%>` | metrics | |

> Separate clearly: **measured on real customer docs** vs **synthetic/mock**.

## 3. Limitations observed

- `<retrieval gaps, format gaps, latency, language edge cases>`
- Known architectural limits: single-node, no HA, manual recovery.

## 4. User feedback (anonymized, no PII)

- `<paraphrased themes; no raw identities>`

## 5. Incidents during PoC

| Date (UTC) | Severity | Summary (non-sensitive) | Resolution |
| --- | --- | --- | --- |
| `<...>` | `<SEV>` | `<...>` | `<...>` |

## 6. Unresolved risks

- `<security review item / IdP validation / capacity / data coverage>`

## 7. Pricing & renewal proposal (ASSUMPTIONS — not a quote)

- Annual license assumption: `<¥...>`  · Support tier: business-hours
- Stated assumptions: `<seats, corpora size, host provided by customer>`
- 24×7 / HA: **future/optional add-on, not included**

## 8. Expansion plan

- Phase 1 (annual): `<scope>`  · Phase 2: `<departments / formats>`

## 9. Go / No-Go checklist

- [ ] Real-document eval meets agreed bar (`<bar>`), measured not assumed
- [ ] SSO validated against the customer's real IdP (not just mock)
- [ ] Monitoring/alerting acceptance signed off
- [ ] DR restore test performed on the real host
- [ ] Security review items closed or accepted with owners
- [ ] Pricing/assumptions agreed in writing
- Decision: `<GO / NO-GO / EXTEND>`  · Owner: `<...>`  · Date: `<...>`
