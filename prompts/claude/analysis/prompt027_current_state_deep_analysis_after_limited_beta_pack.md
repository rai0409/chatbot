# Prompt027: Current State Deep Analysis After Limited Beta Pack

You are working in:

/home/rai/chatbot

## Goal

Analyze the repository state after Prompt026 and produce a precise, evidence-based current-state report.

This is an analysis-only prompt. Do not implement product features. Do not change runtime behavior. Do not modify source code unless a tiny report-generation helper is absolutely necessary, and prefer not to.

The report must answer:

- What has been completed so far?
- What is actually verified by tests, evals, scripts, tags, and artifacts?
- What is ready for limited external beta?
- What is not ready for general production?
- What blockers remain?
- What should the next implementation prompt target?
- Which past claims are supported by repo evidence, and which are only assumptions?

## Execution mode

Proceed autonomously.

Commit and tag automatically only if the prompt reaches PASS and the git diff is limited to analysis/report artifacts.

Stop only for destructive operations, user-data deletion, secrets/.env access, remote push/deploy, production vectorstore/default collection mutation, required network/model downloads, ambiguous missing targets, or unresolved verification failure after one bounded fix attempt.

Do not read .env.
Do not print or infer secrets.
Do not download models.
Do not run Prompt020.
Do not change cross-encoder settings.
Do not change distance thresholds.
Do not change tenant authorization semantics.
Do not change rate-limiter semantics.
Do not change the too_general guard.
Do not mutate production/default vectorstore.
Do not use real customer data.
Do not push remotely.
Do not deploy externally.
No new dependencies.

## Preconditions to verify

Verify and record evidence for these tags if present:

- prompt013-security-tenant-authorization
- prompt014-phase5a-cross-encoder-rerank
- prompt015-phase5b-qa-pair-chunks
- prompt016-phase5c-eval-corpus-expansion
- prompt017-phase5d-guard-calibration
- prompt018-multiformat-ingestion-foundation
- prompt021-phase5f-too-general-guard-redesign
- prompt022-multiformat-onboarding
- prompt023-deploy-ops
- prompt024-security-ops
- prompt025-observability-beta-gate
- prompt026-limited-beta-launch-pack

Also verify current branch, HEAD, working tree status, and whether there are untracked files that should be ignored for this analysis.

## Analysis scope

### 1. Timeline and evidence map

Create a timeline table from Prompt013 through Prompt026.

For each prompt/tag, include:

- commit hash
- tag name
- main files added or modified
- main capability added
- verification evidence
- whether it affects limited beta readiness
- whether it affects general production readiness
- any known limitations

### 2. Capability inventory

Analyze the current repository and classify capabilities into these buckets:

- Retrieval and citation quality
- Approved Q&A exact-match route
- QA-pair chunking
- Real corpus and eval coverage
- Guard / abstain behavior
- Multi-format ingestion
- Dry-run onboarding and import manifest
- Tenant isolation and tenant authorization
- API auth and admin/search debug handling
- Rate limiting
- Deploy smoke
- Backup and restore
- TLS/reverse proxy documentation
- Audit and log retention
- Metrics and Prometheus export
- Alert thresholds
- Production readiness report
- Limited beta launch checklist
- Rollback runbook
- Pilot tenant onboarding runbook
- Limited beta preflight script

For each capability, state:

- ready / partial / not ready
- evidence files
- tests or evals proving it
- remaining risks

### 3. Limited beta readiness assessment

Using repo evidence only, answer whether the current repository is ready for limited external beta.

Use one of:

- GO
- GO with conditions
- NO-GO

If GO with conditions, list mandatory conditions. Include at minimum:

- production_safe profile or equivalent safe route
- API auth enabled
- per-tenant API keys
- API_AUTH_TENANT_MAP configured
- RATE_LIMIT_ENABLED enabled
- ADMIN_AUTH_ENABLED enabled
- SEARCH_DEBUG_ENABLED false
- TLS termination
- named pilot tenant allowlist
- human-in-the-loop review
- knowledge manifest review
- backup before launch
- restore rehearsal
- live deploy smoke
- alert threshold wiring
- rollback owner and procedure

Do not claim general production readiness if evidence does not support it.

### 4. General production blocker analysis

Identify blockers that prevent general production readiness.

Analyze at least these:

- /chat tenant runtime wiring
- durable multi-tenant persistence
- cross-encoder rerank promotion decision
- production/default vectorstore handling
- post-deploy smoke automation
- automated rollback path
- actual external secret store integration
- distributed rate limiting
- operational ownership
- real customer data onboarding controls
- monitoring and alerting implementation beyond documentation
- any remaining test/eval gaps

For each blocker, include:

- why it matters
- current evidence
- risk level: high / medium / low
- suggested next prompt target
- whether it should be before or after limited beta

### 5. Verification commands

Run safe verification commands only.

Required:

- git status --short
- git log --oneline --decorate -20
- git tag --list with prompt tags
- python -m pytest --collect-only -q
- python -m pytest tests/test_embedding_fingerprint.py tests/test_guard_distance_calibration.py -q
- python -m pytest tests/test_rate_limit.py tests/test_metrics_observability.py tests/test_observability_export.py tests/test_production_readiness_report.py -q
- scripts/product_readiness_smoke.sh
- scripts/limited_beta_preflight.sh

If Docker is available and safe, do not run Docker smoke automatically unless it is already clearly safe and non-destructive. Prefer documenting the command:

- scripts/limited_beta_preflight.sh --with-docker-smoke

Do not run commands that read .env. Do not run commands that mutate production/default vectorstore.

### 6. Output report

Create:

- docs/reports/current_state_after_prompt026_limited_beta_pack.md
- artifacts/readiness/current_state_after_prompt026_summary.json

The markdown report must include:

- Executive summary
- Evidence-backed timeline
- Completed capabilities
- Verified test/eval/script status
- Limited beta readiness decision
- General production blockers
- Recommended next prompt
- Risk register
- Exact commands used
- Unknowns and assumptions
- Do-not-claim list

The JSON summary must include:

- branch
- head_commit
- tags_detected
- current_decision
- completed_capabilities
- partial_capabilities
- blockers
- recommended_next_prompt
- verification_results
- generated_at

Do not include secrets, raw API keys, private data, or .env contents.

## Recommended next prompt decision

At the end, recommend exactly one next implementation prompt.

Use this decision rule:

- If /chat tenant runtime wiring is still unproven or partial, recommend that as the next prompt.
- Else if durable multi-tenant persistence is still unproven or partial, recommend that as the next prompt.
- Else if cross-encoder rerank promotion is still parked because model is not cached, recommend either a local-cache verification prompt or keep it parked.
- Else recommend post-deploy automation and rollback automation.

The recommendation must be specific enough to become the next Claude Code prompt.

## Commit/tag policy

PASS:

- commit message: `analysis current state after prompt026 limited beta pack`
- tag: `analysis-current-state-after-prompt026-limited-beta-pack`

PARTIAL or FAIL:

- no commit
- no tag
- report blocker and next command

## Required final output

1. Preconditions
2. Analysis summary
3. Verification results
4. Limited beta readiness decision
5. General production blockers
6. Recommended next prompt
7. Report paths
8. Git diff summary
9. Commit/tag result
10. Final judgment: PASS / PARTIAL / FAIL
