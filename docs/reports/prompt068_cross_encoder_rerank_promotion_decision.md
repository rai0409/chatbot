# Prompt068: Cross-Encoder Rerank Promotion Decision

ANALYSIS / decision only. **No threshold/setting change, no model download, no
runtime change.** Decides whether to promote, park, or gate the cross-encoder
reranker.

## 1. Preconditions

- `rag_core/cross_encoder_reranker.py`: **default-off**
  (`CROSS_ENCODER_RERANK_ENABLED`), runs AFTER the heuristic ranking, knobs
  `CROSS_ENCODER_MODEL` and `CROSS_ENCODER_TOP_N` (default 20). Raises a clear
  install/disable error if the library/model is absent.
- Product route policy keeps LLM-rerank in `_NEVER_ENABLE_FEATURES`; the
  cross-encoder is the only rerank candidate under consideration.
- Targeted tests green (**10 passed**) — the code path is correct **when a model
  is provided**; tests use a stub/mock, not a downloaded model.

## 2. Current state

- Implemented, isolated, additive, and parked: it re-scores the top-N heuristic
  results only; with it off, behavior is exactly today's heuristic ranking.
- The model is **not cached locally** — enabling it without a cached model fails
  fast by design (no silent network fetch, important for offline/on-prem).
- A prior promotion-eval (Prompt020) exists as the evaluation harness; no
  real-corpus measured uplift is on record here.

## 3. Quality vs latency / cost / dependency

- **Quality (expected, not measured here)**: cross-encoders typically improve
  top-k ordering on ambiguous queries, but uplift is corpus-specific and
  **unmeasured on real customer documents** — no accuracy claim.
- **Latency/cost**: adds a synchronous per-query model inference over up to
  `TOP_N` candidates → meaningful added latency, especially CPU-only on-prem
  hosts without a GPU. Competes with the business-hours single-node latency
  budget (Prompt066 capacity signals).
- **Dependency/offline**: requires the cross-encoder model artifact present on the
  host. For air-gapped/on-prem customers the model must be **pre-staged in the
  release bundle** (ties to Prompt061 packaging) — no runtime download.

## 4. On-prem caching options

| Option | Description | Trade-off |
| --- | --- | --- |
| **Park (current)** | off; heuristic ranking only | zero risk, zero added latency; foregoes potential uplift |
| **Bundle + opt-in per deployment** | ship model in release; enable only where latency budget + measured uplift justify | needs eval + larger bundle + per-host validation |
| **GPU-assisted** | enable where a GPU exists | infra-dependent; out of default scope |

## 5. Recommendation

- **Stay parked (default-off) for the first annual contract.** No measured
  real-corpus uplift justifies the added latency and the model-staging/offline
  burden today. The abstain-first, citation-grounded heuristic path is the
  validated behavior.
- **Do not change any cross-encoder setting or threshold now.**
- Revisit only via a measured promotion-eval if a design partner shows ranking
  quality as the limiting factor.

### Promotion-eval done-criteria (if pursued later)

1. Measured top-k quality uplift on a **real (or representative synthetic)**
   corpus using the Prompt020 harness, vs the heuristic baseline.
2. p50/p95 latency measured **on the target on-prem host class** within the
   support latency budget.
3. Model artifact pre-staged in the offline release bundle (no runtime fetch);
   release-check (Prompt061) updated.
4. Tenant isolation + citation correctness unchanged (regression suite green).
5. Documented enable/rollback procedure; default stays off until per-deployment
   sign-off.

## 6. Safety / no-secret / no-customer-data result

- No settings changed, no model downloaded, no network call. No secrets, no
  customer data, no `.env` access. Orphan files untouched.

## 7. Verification results

- Targeted: `tests/test_cross_encoder_rerank.py` → **10 passed**.
- `pytest --collect-only -q`: **860 collected**. Full suite **not run** (analysis-
  only; no source change).

## 8. Final judgment: PASS

## 9. Next recommendation

Prompt069 — general production-readiness gap reassessment (analysis, strict labels).
