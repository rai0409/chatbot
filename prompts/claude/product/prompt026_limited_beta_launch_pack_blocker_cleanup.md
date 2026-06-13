# Prompt026: Limited Beta Launch Pack / Blocker Cleanup

You are working in:

/home/rai/chatbot

## Goal

Prepare the repository for a limited external beta by cleaning up the known pre-beta blocker and packaging the operational launch workflow.

This prompt follows Prompt025, which produced a GO-with-conditions beta assessment. Your job is to close or sharply document the remaining beta blockers without changing product behavior outside this prompt scope.

## Execution mode

Proceed autonomously.

Commit and tag automatically only if this prompt reaches PASS and the git diff is limited to this prompt scope.

Stop only for destructive operations, user-data deletion, secrets/.env access, remote push/deploy, production vectorstore/default collection mutation, required network/model downloads, ambiguous missing targets, or unresolved verification failure after one bounded fix attempt.

Do not read .env.
Do not print or infer secrets.
Do not download models.
Do not run Prompt020.
Do not change cross-encoder settings.
Do not change global distance thresholds.
Do not change tenant authorization semantics.
Do not change rate-limiter semantics.
Do not change the too_general guard.
Do not use real customer data.
Do not push remotely.
Do not deploy externally.
No new dependencies.

## Preconditions to verify before implementing

Verify all of the following before implementation:

- Prompt025 is complete.
- Tag `prompt025-observability-beta-gate` exists.
- Prompt025 artifacts exist:
  - `docs/reports/beta_go_no_go_assessment.md`
  - `artifacts/readiness/production_readiness_report.json`
  - `artifacts/readiness/production_readiness_report.md`
- Security and operations foundations exist:
  - `docs/security_operations.md`
  - `docs/operations.md`
  - `scripts/deploy_smoke.sh`
  - `scripts/backup.sh`
  - `scripts/restore.sh`
  - `scripts/onboard_documents_dry_run.py`
  - `scripts/import_manifest.py`
- The known embedding fingerprint blocker either still reproduces or has already been fixed:
  - `tests/test_embedding_fingerprint.py::test_ingest_stamps_collection_fingerprint`

## Scope

### 1. Fix the known embedding fingerprint blocker if it still reproduces

The known failure is:

- `tests/test_embedding_fingerprint.py::test_ingest_stamps_collection_fingerprint`
- observed failure: `KeyError: 'hnsw:space'`

Root issue:

Fingerprint stamping in the ingest/store path must preserve existing collection metadata such as `hnsw:space` while still avoiding Chroma modify errors caused by echoing immutable `hnsw:*` keys into `collection.modify()`.

Implement the narrowest safe fix so that:

- In-memory or fake collection metadata preserves `hnsw:space`.
- Real Chroma `collection.modify()` is not passed immutable `hnsw:*` keys.
- Existing Prompt017 behavior remains valid.
- `tests/test_guard_distance_calibration.py::test_stamp_collection_fingerprint_strips_hnsw_keys` still passes or is updated only if its original intent remains preserved.
- No production/default vectorstore is mutated.

### 2. Create a limited beta launch checklist

Add:

- `docs/reports/limited_beta_launch_checklist.md`

It must include a command-checkable checklist covering:

- required env toggles, using placeholders only:
  - `API_AUTH_ENABLED=true`
  - `API_AUTH_KEYS`
  - `API_AUTH_TENANT_MAP`
  - `ADMIN_AUTH_ENABLED=true`
  - `SEARCH_DEBUG_ENABLED=false`
  - `RATE_LIMIT_ENABLED=true`
- tenant map requirement
- TLS termination requirement
- dry-run onboarding requirement
- import manifest review
- knowledge manifest review
- backup before launch
- restore rehearsal
- live deploy smoke
- `/health`
- `/metrics?format=prometheus`
- alert threshold wiring
- pilot tenant allowlist
- human-in-the-loop review process
- rollback owner and rollback command references
- no real data in tests or smokes
- no raw API keys in docs, logs, metrics, or examples

### 3. Create a rollback runbook

Add:

- `docs/reports/limited_beta_rollback_runbook.md`

It must include:

- rollback triggers
- immediate containment steps
- disabling traffic at the reverse proxy
- disabling or rotating compromised keys
- restoring from hash-verified backup
- reverting to a previous local git tag
- re-running smoke checks after rollback
- audit-log review window
- customer/pilot communication template with placeholders only
- no secrets or real tenant names

### 4. Create a pilot tenant onboarding runbook

Add:

- `docs/reports/pilot_tenant_onboarding_runbook.md`

It must describe:

- collecting synthetic or sanitized pilot documents first
- running `scripts/onboard_documents_dry_run.py`
- reviewing `runs/onboarding/<tenant>/manifest.json`
- detecting duplicate IDs and duplicate text
- checking tenant mismatch
- approving the knowledge manifest
- ingesting only into an explicit non-production or pilot collection
- verifying with a small approved-QA smoke
- documenting pilot scope and exit criteria

### 5. Create a limited beta verification script

Add:

- `scripts/limited_beta_preflight.sh`

It must be safe by default and must not read `.env`.

Required behavior:

- Bash script with strict mode.
- Validates repo-local required files exist.
- Validates required tags exist:
  - `prompt023-deploy-ops`
  - `prompt024-security-ops`
  - `prompt025-observability-beta-gate`
- Runs safe local checks only:
  - targeted pytest for embedding fingerprint, rate limit, metrics, observability export, and production readiness
  - product readiness smoke if safe
  - smoke eval
  - qa_pair eval
- Checks generated readiness artifacts exist.
- Checks beta checklist and runbook docs exist.
- Does not require Docker by default.
- Supports optional `--with-docker-smoke` to run `scripts/deploy_smoke.sh`.
- Must not print secrets.
- Must not read `.env`.
- Must not touch production/default vectorstore.
- Exits non-zero on failure.

### 6. Update beta assessment minimally

Update:

- `docs/reports/beta_go_no_go_assessment.md`

Only if necessary, reflect the blocker cleanup and point to the new checklist, runbooks, and preflight script.

Do not weaken the GO-with-conditions constraints.

## Explicit non-goals

- Actual external beta launch.
- Remote deploy.
- Remote push.
- Real customer data.
- Secret manager integration.
- OAuth/JWT.
- Distributed rate limiting.
- Cross-encoder promotion.
- Production/default vectorstore mutation.
- General production readiness claim.

## Verification

Run these targeted checks first:

    python -m pytest tests/test_embedding_fingerprint.py tests/test_guard_distance_calibration.py -q
    python -m pytest tests/test_rate_limit.py tests/test_metrics_observability.py tests/test_observability_export.py tests/test_production_readiness_report.py -q
    bash -n scripts/limited_beta_preflight.sh

Then run these broader checks:

    python -m pytest --collect-only -q

    PYTHONPATH=. .venv/bin/python -m eval.runner --cases eval/cases/smoke_cases.jsonl --chunks-jsonl eval/cases/smoke_chunks.jsonl --output runs/eval/prompt026_smoke_check.json

    PYTHONPATH=. .venv/bin/python -m eval.runner --cases eval/cases/qa_pair_cases.jsonl --chunks-jsonl eval/cases/qa_pair_chunks.jsonl --output runs/eval/prompt026_qa_pair_check.json

    scripts/product_readiness_smoke.sh

    scripts/limited_beta_preflight.sh

Optional only if Docker is available and safe:

    scripts/limited_beta_preflight.sh --with-docker-smoke

## Commit/tag policy

PASS:

- commit message: `prompt026 limited beta launch pack blocker cleanup`
- tag: `prompt026-limited-beta-launch-pack`

PARTIAL or FAIL:

- no commit
- no tag
- report blocker and next command

## Required final output

1. Preconditions
2. Blocker cleanup result
3. Implementation summary
4. Verification results
5. Beta readiness delta
6. Git diff summary
7. Commit/tag result
8. Final judgment: PASS / PARTIAL / FAIL
9. Next recommendation
