# Beta Go / No-Go Assessment

Final gate document for batch **B6** (observability + beta gate) of the
autonomous plan in
`docs/reports/current_state_chatbot_direction_autonomous_plan.md`. It
aggregates the results of batches B1–B6 and gives an explicit recommendation
for a **limited external beta**. It is not a full production-approval sign-off
— that still requires human review and deployment-specific validation.

Generated alongside the regenerated static readiness report at
`artifacts/readiness/production_readiness_report.{json,md}` (decision at time
of writing: **`needs_review`** — critical static safety checks pass; open
items are review/deployment-configuration warnings, not code blockers).

## Blocker cleanup (Prompt026)

- The known pre-beta test blocker
  (`tests/test_embedding_fingerprint.py::test_ingest_stamps_collection_fingerprint`,
  `KeyError: 'hnsw:space'`) is **closed**: the fingerprint-stamping path
  already strips immutable `hnsw:*` keys before `collection.modify()`; the
  test's fake collection was corrected to model real Chroma (merge + preserve
  the immutable `hnsw` config) instead of replacing metadata. No production or
  default vectorstore is mutated.
- The operational launch workflow is now packaged:
  - `docs/reports/limited_beta_launch_checklist.md`
  - `docs/reports/limited_beta_rollback_runbook.md`
  - `docs/reports/pilot_tenant_onboarding_runbook.md`
  - `scripts/limited_beta_preflight.sh` (safe-by-default repo-local preflight)

These close the launch-readiness packaging gap but **do not** weaken any
condition below; the recommendation remains GO **with conditions** for a
limited beta only.

## Batch outcomes (B1–B6)

| Batch | Scope | Status | Evidence |
| --- | --- | --- | --- |
| **B1** | Real-vector confidence-guard calibration | Closed | `prompt017-phase5d-guard-calibration`; guard distance calibration tests + measured `too_general` redesign (`prompt021-phase6f-...`) |
| **B2** | Cross-encoder rerank + promotion gate | Partially closed | `prompt014-phase5a-cross-encoder-rerank`; promotion **gate present**, but no promotion-decision artifact yet — rerank stays **off by default** (safe) |
| **B3** | Tenant onboarding & data pipeline | Closed (preview scope) | `prompt013-security-tenant-authorization`, multiformat ingestion (`prompt018`), onboarding dry-run (`prompt022-multiformat-onboarding`) |
| **B4** | Deployment operations pack | Closed | `prompt023-deploy-ops`: deploy smoke, backup/restore, TLS reference, log retention |
| **B5** | Security operations pack | Closed | `prompt024-security-ops`: default-off rate limiting, key-rotation runbook, secrets handling (`docs/security_operations.md`) |
| **B6** | Observability export + beta gate | Closed (this batch) | Prometheus `/metrics?format=prometheus`, 429/auth-rejection counters, alert thresholds, readiness regeneration, this document |

## What closed in B1–B6

- **Confidence guard** calibrated on real vectors; the `too_general` guard
  redesigned with measured thresholds (no blind threshold changes).
- **API auth**: API-key authentication with optional per-key→tenant
  authorization that fails closed; raw keys never appear in logs, metrics,
  audit events, errors, or limiter state.
- **Rate limiting**: in-process, default-off, fingerprint-keyed, 429 +
  `Retry-After`, enforced after auth.
- **Deploy ops**: container deploy smoke proving the image carries no
  secrets, backup/restore with manifest verification, reverse-proxy/TLS
  reference, audit log retention guidance.
- **Observability**: per-process counters in JSON and Prometheus text
  formats, including new `api_rate_limited_total` and
  `api_auth_rejection_total`; documented alert thresholds mapped to counters.
- **Static readiness report** now observes the security-ops items (auth
  guard, tenant authorization, rate-limit guard, runbook doc).

## Open blockers / conditions

These remain open and bound the beta scope (from the readiness report's
known blockers and review warnings):

1. **`/chat` tenant runtime wiring not enabled** — multi-tenant runtime
   serving on `/chat` is not fully wired; the safe path is the
   preview/product flow with `production_safe`.
2. **DB persistence / tenant isolation not fully production-grade** —
   isolation is enforced at the retrieval/auth layer, but durable
   multi-tenant persistence is not complete.
3. **Cross-encoder / feature rerank promotion not decided** — gate exists,
   decision artifact absent; rerank stays disabled by default.
4. **Generated knowledge manifest needs deployment review** — manifest
   builder present; a generated manifest should be produced and reviewed per
   deployment corpus.
5. **Rollback / profile-promotion workflow is still manual/config-based.**
6. **End-to-end deployed-server smoke is manual** — `deploy_smoke.sh` covers
   the container contract; a live, behind-TLS smoke is still operator-run.
7. **Admin/auth/rate-limit env must be configured at deploy time** —
   `ADMIN_AUTH_ENABLED`, `API_AUTH_ENABLED` + keys, `RATE_LIMIT_ENABLED` are
   off by default and must be turned on for any exposed deployment.

## Recommendation: GO, with conditions (limited external beta)

A **limited external beta** is justified — the auth, rate-limiting, deploy,
and observability foundations needed to expose an endpoint to external
callers are in place — **provided every condition below is met**. This is
**not** approval for general production or unrestricted multi-tenant traffic.

**Mandatory conditions for the beta:**

1. Serve via the **`production_safe`** profile; keep similar auto-answer,
   LLM answer/rerank, and debug comparison **off**. Do not enable feature
   rerank without a recorded promotion decision (blocker 3).
2. Set, on the deployed instance: `API_AUTH_ENABLED=true` with per-tenant
   `API_AUTH_KEYS`, `API_AUTH_TENANT_MAP` for every served tenant,
   `RATE_LIMIT_ENABLED=true` with a budget sized for the worker count, and
   `ADMIN_AUTH_ENABLED=true` with a non-empty `ADMIN_AUTH_TOKEN`. Keep
   `SEARCH_DEBUG_ENABLED=false`.
3. Terminate TLS in front of the container (see `docs/operations.md`); never
   expose `:8000` directly.
4. Limit scope: a **small, named set of pilot tenants** with a
   human-in-the-loop review queue; no open self-serve signup.
5. Generate and review the **knowledge manifest** for the beta corpus
   (blocker 4).
6. Wire alerting on the documented thresholds (provider-error, fallback,
   guard-trip, 429, auth-rejection, `/health`), accounting for the
   per-process counter caveat.
7. Run the **manual live smoke** (rotated-key checks in
   `docs/security_operations.md`, deploy smoke for the container) against the
   actual beta deployment before opening it.
8. Have a **rollback plan** ready (config/profile revert + restore from
   backup), since the workflow is still manual (blocker 5).

**No-go for:** general production, unrestricted multi-tenant `/chat` runtime
serving, or any deployment that cannot meet the conditions above.

## Re-evaluation triggers

Promote beyond limited beta only after: `/chat` tenant runtime wiring and
durable persistence land (blockers 1–2), a recorded rerank promotion
decision (3), an automated post-deploy smoke (6), and an automated rollback
path (5). Before the first pilot caller, work through
`docs/reports/limited_beta_launch_checklist.md` and run
`scripts/limited_beta_preflight.sh` (exit 0). Re-run
`eval/production_readiness_report.py` and update this
document at that point.
