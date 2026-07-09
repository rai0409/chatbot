# Current State Deep Analysis — After Prompt026 (Limited Beta Pack)

Analysis-only report (Prompt027). No runtime behavior was changed; only this
report and its JSON summary were produced. All claims below are backed by repo
evidence (tags, commits, tests, scripts, artifacts) gathered on the date in the
JSON summary.

- Branch: `eval/real-vector-evidence`
- HEAD: `7ad3cde` (`prompt026 limited beta launch pack blocker cleanup`)
- Working tree: clean except one untracked analysis prompt file
  (`prompts/claude/analysis/prompt027_current_state_deep_analysis_after_limited_beta_pack.md`),
  ignorable for this analysis.

## 1. Executive summary

The repository is **ready for a limited external beta under strict conditions
(GO with conditions)** and is **NOT ready for general production**.

Since Prompt013, the project added: API-key auth + per-key→tenant
authorization, cross-encoder rerank (off by default), QA-pair chunking, a real
eval corpus + builder, real-vector guard calibration, a measured `too_general`
guard redesign, multi-format ingestion + dry-run onboarding + import manifest,
a deploy-ops pack (smoke/backup/restore/TLS docs), a security-ops pack
(default-off rate limiting, key-rotation runbook, secrets handling),
observability (JSON + Prometheus metrics, 429/auth-rejection counters, alert
thresholds), a regenerated static readiness report, and a full limited-beta
launch pack (checklist, rollback runbook, pilot onboarding runbook, preflight
script). The one known repo-local test blocker (embedding fingerprint
`hnsw:space`) was closed in Prompt026.

Verification at HEAD: **700 tests pass, 0 fail**; `product_readiness_smoke.sh`
and `limited_beta_preflight.sh` both exit 0; smoke eval 21/21 and qa_pair eval
7/7 (synthetic data). The static readiness report decision is
**`needs_review`** with **no critical blockers** — open items are
deployment-time configuration warnings and optional artifacts.

General production is blocked primarily by: `/chat` per-tenant **profile/policy**
runtime selection (isolation/authorization *are* wired and tested; per-tenant
profile resolution is preview-only), durable multi-tenant persistence
(single-node local Chroma file store), an undecided cross-encoder rerank
promotion, and the fact that monitoring/rollback are documented but not
automated.

## 2. Evidence-backed timeline (Prompt013 → Prompt026)

| Tag | Commit | Main files | Capability added | Verification | Limited-beta impact | Gen-prod impact | Known limitation |
| --- | --- | --- | --- | --- | --- | --- | --- |
| prompt013-security-tenant-authorization | `eaff218` | `webapi/api_auth.py`, `webapi/main.py`, `tests/test_api_key_tenant_authorization.py` | API-key auth + per-key→tenant authorization (fail-closed) | `test_api_key_tenant_authorization.py` (in 700-pass suite) | Enables gated multi-tenant exposure | Necessary, not sufficient | Authorization only; no per-tenant profile selection on `/chat` |
| prompt014-phase5a-cross-encoder-rerank | `50d896b` | `rag_core/cross_encoder_reranker.py`, `eval/runner.py`, `config.py` | Cross-encoder rerank stage (default OFF) | `tests/test_cross_encoder_rerank.py` | Neutral (off) | Promotion undecided | Needs cached model + promotion decision; parked |
| prompt015-phase5b-qa-pair-chunks | `f2de537` | `scripts/approved_qa_to_pair_chunks.py`, `eval/cases/qa_pair_*.jsonl` | QA-pair chunking + eval cases | `tests/test_qa_pair_chunks.py`; qa_pair eval 7/7 | Improves answer quality | Same | Small curated set |
| prompt016-phase5c-eval-corpus-expansion | `49a7695` | `eval/cases/real_corpus_*.jsonl`, `scripts/build_eval_cases_from_approved_qa.py` | Real eval corpus + builder | `tests/test_build_eval_cases.py` | Raises confidence | Same | Corpus still modest |
| prompt017-phase5d-guard-calibration | `d32d58f` | `eval/guard_distance_calibration.py`, `rag_core/store.py`, `tests/test_guard_distance_calibration.py` | Real-vector guard distance calibration (measurement-only) | `test_guard_distance_calibration.py` | Safer abstain | Same | Calibration artifacts not auto-read at runtime (by design) |
| prompt018-multiformat-ingestion-foundation | `86e4adf` | `rag_core/document_converters/*`, `scripts/convert_document_to_canonical_jsonl.py` | PDF/DOCX/PPTX/XLSX/CSV converters | `tests/test_document_converters.py` | Enables tenant corpora | Same | Conversion quality varies by source |
| prompt021-phase5f-too-general-guard-redesign | `a765b78` | `rag_core/qa.py`, `eval/too_general_guard_analysis.py`, `tests/test_too_general_guard_redesign.py` | Measured `too_general` guard redesign | `test_too_general_guard_redesign.py`; smoke eval 21/21 | Fewer bad answers | Same | Tuned to current corpus |
| prompt022-multiformat-onboarding | `4e641a9` | `scripts/onboard_documents_dry_run.py`, `scripts/import_manifest.py`, `eval/cases/sample_docs/*` | Dry-run onboarding + import manifest (dup/tenant/collision checks) | `tests/test_multiformat_onboarding.py` | Core to pilot onboarding | Additive | Manual manifest review |
| prompt023-deploy-ops | `da36340` | `scripts/deploy_smoke.sh`, `scripts/backup.sh`, `scripts/restore.sh`, `docs/operations.md` | Deploy smoke, backup/restore, TLS/reverse-proxy docs, log retention | `tests/test_deploy_ops.py` | Required ops baseline | Partial (manual) | Live smoke is operator-run |
| prompt024-security-ops | `407d256` | `webapi/rate_limit.py`, `docs/security_operations.md`, `tests/test_rate_limit.py` | Default-off in-process rate limiting, key-rotation runbook, secrets handling | `test_rate_limit.py` | Required for exposure | Single-node only | Per-process limiter; not distributed |
| prompt025-observability-beta-gate | `371d341` | `webapi/metrics_registry.py`, `webapi/main.py`, `eval/production_readiness_report.py`, `docs/reports/beta_go_no_go_assessment.md`, `artifacts/readiness/*` | JSON+Prometheus metrics, 429/auth counters, alert thresholds, readiness report, beta go/no-go | `test_metrics_observability.py`, `test_observability_export.py`, `test_production_readiness_report.py` | Required for exposure | Docs/exports only | Per-process counters; alerts documented not wired |
| prompt026-limited-beta-launch-pack | `7ad3cde` | `docs/reports/limited_beta_*`, `docs/reports/pilot_tenant_onboarding_runbook.md`, `scripts/limited_beta_preflight.sh`, `tests/test_embedding_fingerprint.py` | Launch checklist, rollback runbook, pilot onboarding runbook, preflight script; closed embedding-fingerprint blocker | `test_embedding_fingerprint.py` (fixed); `limited_beta_preflight.sh` exit 0 | Completes launch packaging | Process docs only | Checklist is manual; no live deployment exercised |

(Inter-batch analysis commits `1300f86`, `6863237`, `4d12f8b` and the
`fedd34d` roadmap/corpus build commit are non-feature and are excluded.)

## 3. Completed capabilities (inventory)

Legend: **Ready** = implemented + test/eval/script evidence; **Partial** =
implemented but gated/manual/limited; **Not ready** = not implemented for
general production.

| Capability | Status | Evidence files | Tests/evals/scripts | Remaining risk |
| --- | --- | --- | --- | --- |
| Retrieval & citation quality | Ready (beta) | `rag_core/qa.py`, `rag_core/retrieval.py`, `rag_core/source_metadata.py` | smoke eval 21/21; `test_no_answer_citations.py`, `test_source_metadata.py` | Corpus-size dependent |
| Approved Q&A exact-match route | Ready | `rag_core/approved_qa.py`, `webapi/main.py` | `test_approved_qa*.py`; deploy smoke exact-match | Requires curated approved set |
| QA-pair chunking | Ready | `scripts/approved_qa_to_pair_chunks.py`, `eval/cases/qa_pair_*` | qa_pair eval 7/7; `test_qa_pair_chunks.py` | Small set |
| Real corpus & eval coverage | Partial | `eval/cases/real_corpus_*`, `scripts/build_eval_cases_from_approved_qa.py` | `test_build_eval_cases.py` | Modest corpus; not production-scale |
| Guard / abstain behavior | Ready (beta) | `rag_core/qa.py`, `eval/guard_distance_calibration.py`, `eval/too_general_guard_analysis.py` | `test_too_general_guard_redesign.py`, `test_guard_distance_calibration.py`, `test_confidence_guard.py` | Tuned to current corpus |
| Multi-format ingestion | Ready | `rag_core/document_converters/*` | `test_document_converters.py` | Source-quality variance |
| Dry-run onboarding & import manifest | Ready | `scripts/onboard_documents_dry_run.py`, `scripts/import_manifest.py` | `test_multiformat_onboarding.py` | Manual review step |
| Tenant isolation & authorization | Ready (beta) | `webapi/api_auth.py`, `rag_core/retrieval.py` | `test_tenant_isolation.py`, `test_api_key_tenant_authorization.py` (27 passed) | Metadata-filter isolation, single store |
| API auth & admin/search-debug handling | Ready | `webapi/api_auth.py`, `webapi/admin_auth.py` | `test_api_auth.py`, `test_admin_auth.py` | Must be enabled per deployment |
| Rate limiting | Ready (beta) / Partial (prod) | `webapi/rate_limit.py` | `test_rate_limit.py` | Per-process, not distributed |
| Deploy smoke | Ready | `scripts/deploy_smoke.sh` | `test_deploy_ops.py` | Operator-run, not in CI |
| Backup & restore | Ready | `scripts/backup.sh`, `scripts/restore.sh` | `test_deploy_ops.py` (hash-verified restore) | Manual cadence |
| TLS / reverse-proxy docs | Ready (doc) | `docs/operations.md` | n/a (reference configs) | Deployment-specific |
| Audit & log retention | Ready | `rag_core/audit_log.py`, `docs/operations.md` | `test_*` audit paths in suite | Retention is policy guidance |
| Metrics & Prometheus export | Ready | `webapi/metrics_registry.py`, `webapi/main.py` | `test_metrics_observability.py`, `test_observability_export.py` | Per-process counters |
| Alert thresholds | Partial | `docs/operations.md` (Alert thresholds) | n/a (documentation) | Not wired to a monitor |
| Production readiness report | Ready | `eval/production_readiness_report.py`, `artifacts/readiness/*` | `test_production_readiness_report.py` | Static checks only |
| Limited beta launch checklist | Ready | `docs/reports/limited_beta_launch_checklist.md` | preflight presence check | Manual checklist |
| Rollback runbook | Ready (doc) | `docs/reports/limited_beta_rollback_runbook.md` | preflight presence check | Manual procedure |
| Pilot tenant onboarding runbook | Ready (doc) | `docs/reports/pilot_tenant_onboarding_runbook.md` | preflight presence check | Manual procedure |
| Limited beta preflight script | Ready | `scripts/limited_beta_preflight.sh` | runs green (exit 0) | Repo-local only by default |

## 4. Verified test / eval / script status (at HEAD `7ad3cde`)

- `pytest --collect-only -q` → **700 tests collected**.
- Full suite `pytest -q` → **700 passed, 0 failed**.
- `pytest tests/test_embedding_fingerprint.py tests/test_guard_distance_calibration.py -q` → **15 passed** (blocker closed; calibration intent preserved).
- `pytest tests/test_rate_limit.py tests/test_metrics_observability.py tests/test_observability_export.py tests/test_production_readiness_report.py -q` → **53 passed**.
- `pytest tests/test_tenant_isolation.py tests/test_api_key_tenant_authorization.py -q` → **27 passed**.
- `scripts/product_readiness_smoke.sh` → exit 0 (**117 passed**).
- `scripts/limited_beta_preflight.sh` → exit 0 (**PREFLIGHT OK**: required files + tags `prompt023/024/025` + 68 targeted tests + readiness smoke + smoke/qa_pair evals + readiness artifacts).
- Smoke eval → **21/21**; QA-pair eval → **7/7** (synthetic data only).
- Static readiness report decision → **`needs_review`**, **blockers: []**, warnings: `admin_auth_env_requires_production_configuration`, `feature_rerank_promotion_decision_missing(_optional)`, `generated_knowledge_manifest_missing(_optional)`, `git_worktree_dirty`. All 16 safety checks `True`.

## 5. Limited beta readiness decision

**GO with conditions.** Repo evidence supports exposing a *limited* external
beta; it does **not** support general production. The following conditions are
**mandatory** (all checkable via `docs/reports/limited_beta_launch_checklist.md`
and `scripts/limited_beta_preflight.sh`):

1. Serve via the **`production_safe`** profile (similar auto-answer, LLM
   answer/rerank, debug comparison all OFF).
2. `API_AUTH_ENABLED=true` with **per-tenant** `API_AUTH_KEYS`.
3. `API_AUTH_TENANT_MAP` configured for every served tenant (unmapped valid
   keys fail closed with 403).
4. `RATE_LIMIT_ENABLED=true`, budget sized for worker count (per-process).
5. `ADMIN_AUTH_ENABLED=true` with a non-empty `ADMIN_AUTH_TOKEN`.
6. `SEARCH_DEBUG_ENABLED=false`.
7. **TLS termination** in front of the container; `:8000` never exposed.
8. **Named pilot tenant allowlist**; no open self-serve signup.
9. **Human-in-the-loop** review queue staffed during the pilot.
10. **Knowledge manifest** generated and reviewed for the beta corpus.
11. **Backup taken before launch** and stored off-host.
12. **Restore rehearsal** (hash-verified) completed.
13. **Live deploy smoke** passes against the actual deployment.
14. **Alert thresholds wired** (provider-error, fallback, guard-trip, 429,
    auth-rejection, `/health`), accounting for the per-process counter caveat.
15. **Rollback owner named** and rollback procedure
    (`limited_beta_rollback_runbook.md`) rehearsed.

## 6. General production blockers

| Blocker | Why it matters | Current evidence | Risk | Suggested next prompt target | Before/after beta |
| --- | --- | --- | --- | --- | --- |
| `/chat` per-tenant profile/policy runtime wiring | Per-tenant safe-profile/policy selection runs only in the preview path; `/chat` threads tenant_id for **isolation/authorization** (wired + tested) but not per-tenant **profile resolution** | `webapi/main.py`: `/chat` calls `normalize_tenant_id` + `enforce_tenant_authorization` and threads `tenant_id`; `resolve_tenant_product_profile`/`use_tenant_profile` used by product-preview only | High | Wire per-tenant profile resolution into `/chat` behind a default-safe flag, with tests | **Before** general prod (beta can use `production_safe` for all) |
| Durable multi-tenant persistence | Single-node local Chroma file store; isolation is metadata-filter based, not separate durable datastores | `rag_core/store.py` uses `chromadb.PersistentClient(path=...)`; no server/HttpClient mode | High | Define durable/managed persistence + per-tenant data lifecycle; prove isolation under restart/restore | After limited beta |
| Cross-encoder rerank promotion decision | Rerank exists but is OFF; no recorded promotion decision artifact | `rag_core/cross_encoder_reranker.py`; readiness warning `feature_rerank_promotion_decision_missing` | Medium | Local-cache verification + promotion eval, or keep parked | After limited beta |
| Production/default vectorstore handling | Onboarding refuses prod/default collection; no managed prod ingest/promotion flow | `scripts/onboard_documents_dry_run.py` refuses prod/default | Medium | Define guarded prod ingest + collection promotion process | After limited beta |
| Post-deploy smoke automation | Deploy/live smoke is operator-run, not automated/CI | `scripts/deploy_smoke.sh`, `scripts/limited_beta_preflight.sh` (manual) | Medium | Automate post-deploy smoke in a pipeline | After limited beta |
| Automated rollback path | Rollback is a manual runbook | `docs/reports/limited_beta_rollback_runbook.md` | Medium | Script the revert+restore+smoke path | After limited beta |
| External secret store integration | Secrets injected via env/file; no managed store integration | `docs/security_operations.md` (documentation only) | Medium | Optional: integrate a managed secret store at deploy | After limited beta |
| Distributed rate limiting | In-process limiter only; multi-worker/instance budget is per-process | `webapi/rate_limit.py` | Medium | Distributed limiter (e.g. shared store) — explicit non-goal so far | After limited beta |
| Operational ownership | On-call/owner is documented as a checklist item, not an org commitment | `limited_beta_launch_checklist.md` §9 | Medium | Assign owner/on-call before launch | Before beta (process) |
| Real customer data onboarding controls | Beta mandates synthetic/sanitized data; no controls for real PII at scale | onboarding runbook + checklist enforce synthetic-only | High (for prod) | Data-handling controls, DPA alignment, deletion flows | After limited beta |
| Monitoring/alerting beyond docs | Thresholds documented; not wired to a live monitor | `docs/operations.md` | Medium | Wire metrics scrape + alerts | Before beta (condition 14) |
| Remaining test/eval gaps | No live-deployment/integration test in CI; evals are synthetic and modest | suite is unit/contract-level; evals synthetic | Medium | Add integration/e2e + larger eval corpus | After limited beta |

## 7. Recommended next prompt

**Wire per-tenant product-profile/policy resolution into the `/chat` runtime
path, behind a default-safe flag, with tests** — the highest-risk
general-production blocker per §6 and the decision rule in the prompt
(`/chat` tenant runtime wiring is partial).

Concretely, the next implementation prompt should:

- Add per-tenant profile resolution to `/chat` and `/chat/stream` using the
  existing `resolve_tenant_product_profile` already used by
  `/chat/product-preview`, gated by a new default-off env flag (e.g.
  `CHAT_USE_TENANT_PROFILE`, default false → behavior unchanged).
- Default unmapped/unknown tenants to `production_safe`; never enable unsafe
  features via request overrides.
- Preserve existing tenant isolation/authorization semantics and the
  too_general guard, cross-encoder settings, and distance thresholds (no
  changes).
- Add tests: `/chat` resolves the mapped tenant profile when the flag is on;
  default-off leaves `/chat` behavior byte-for-byte unchanged; unknown tenant
  falls back to `production_safe`; overrides cannot enable disabled features.
- Verify: targeted tests + `--collect-only` + smoke 21/21 + qa_pair 7/7 +
  `product_readiness_smoke.sh` + `limited_beta_preflight.sh`.
- Do not mutate the production/default vectorstore; no new dependencies.

This is sequenced **before** durable persistence because it is lower-risk,
unblocks tenant-specific `/chat` serving, and keeps the safe default intact.

## 8. Risk register

| Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
| Beta launched without all conditions met | Medium | High | Enforce `limited_beta_launch_checklist.md` + `limited_beta_preflight.sh` sign-off |
| Per-process rate limiter under-protects multi-worker deploy | Medium | Medium | Size budget per worker; proxy-level limit as defense in depth |
| Cross-tenant exposure via misconfigured tenant map | Low | High | Fail-closed authorization (tested); rotated-key smoke before launch |
| Single-node store loss | Low | High | Pre-launch backup + verified restore rehearsal |
| Alert gaps (thresholds documented, not wired) | Medium | Medium | Condition 14 wiring before launch |
| Corpus-tuned guard regresses on new tenant data | Medium | Medium | Dry-run onboarding + manifest review + abstain-first guard |
| Real customer data used prematurely | Low | High | Synthetic-only mandate in runbooks/checklist |

## 9. Exact commands used

```bash
git status --short
git log --oneline --decorate -20
git tag --list   # prompt0* tags verified by git rev-list per tag
.venv/bin/python -m pytest --collect-only -q
.venv/bin/python -m pytest tests/test_embedding_fingerprint.py tests/test_guard_distance_calibration.py -q
.venv/bin/python -m pytest tests/test_rate_limit.py tests/test_metrics_observability.py tests/test_observability_export.py tests/test_production_readiness_report.py -q
.venv/bin/python -m pytest tests/test_tenant_isolation.py tests/test_api_key_tenant_authorization.py -q
.venv/bin/python -m pytest -q
bash scripts/product_readiness_smoke.sh
scripts/limited_beta_preflight.sh
# documented, not run automatically (Docker present but not exercised here):
scripts/limited_beta_preflight.sh --with-docker-smoke
```

## 10. Unknowns and assumptions

- **Live deployment behavior is unverified here.** All checks are repo-local
  with synthetic data; no TLS/proxy/live `/health`/`/metrics` was exercised.
- **Real Chroma `modify()` semantics** are modeled by the corrected test fake;
  not exercised against a live Chroma server in this analysis.
- **Per-process metrics/limiter behavior under N workers** is reasoned from
  code + docs, not load-tested.
- **Cross-encoder model availability** is unknown (model not cached/downloaded
  by policy); rerank remains off and unverified end-to-end.
- **Eval corpus is modest and synthetic**; production-scale accuracy is not
  established.

## 11. Do-not-claim list

- Do **not** claim general production readiness.
- Do **not** claim live-deployment, TLS, or end-to-end integration is verified.
- Do **not** claim cross-encoder rerank is validated or promoted.
- Do **not** claim durable/managed multi-tenant persistence exists.
- Do **not** claim distributed rate limiting or external secret-store
  integration exists.
- Do **not** claim alerting is wired (thresholds are documented only).
- Do **not** claim accuracy at production scale from the current synthetic
  evals.
