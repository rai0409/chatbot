# Prompt056: Safe Collection Promotion Workflow

You are working in:

/home/rai/chatbot
## Context

KuraDen ingests documents via a dry-run import-manifest path into explicit
NON-production collections (Prompt044, webapi/ingestion_jobs.py). There is no
safe, gated workflow to promote a reviewed staging collection to the served
collection. This is a P0 blocker before a paid PoC.

## Goal

Create a safe staging-to-served collection promotion workflow with a validation
gate, tenant-isolation check, backup point, approval report, and rollback -
without ever directly/unsafely mutating the production/default collection.

## Scope

- A helper/module + script that, given a reviewed NON-production staging
  collection, validates it (import-manifest clean: no duplicate ids / tenant
  mismatch / collisions), runs a tenant-isolation check (reuse
  scripts/persistence_isolation_check.sh patterns), takes a backup point
  (scripts/backup.sh), and produces an approval report.
- Promotion targets an explicit named served collection (never the default
  collection name); refuse the production/default collection outright.
- A rollback path that restores the prior served collection from the backup.
- Default-off / operator-invoked; no change to the chat runtime path.

## Required deliverables

- A promotion workflow script/module + an approval report template under
  docs/reports/ or docs/operations/.
- Tests proving: production/default collection refused; validation gate blocks a
  dirty manifest; isolation check runs; rollback restores; synthetic data only.
- docs/reports/prompt056_safe_collection_promotion_workflow.md.

## Tests / checks

    python -m pytest tests/test_safe_collection_promotion.py -q
    python -m pytest tests/test_ingestion_ui_job_status.py tests/test_durable_multitenant_persistence.py -q
    python -m pytest --collect-only -q ; python -m pytest -q
    scripts/limited_beta_preflight.sh


## Execution mode

Proceed autonomously. Do not ask for yes/no confirmation. Run targeted
tests/checks first; run broader tests only when targeted checks pass and runtime
is reasonable; never fabricate test results; if the full suite is not run, say
so. Commit and tag only on PASS with a prompt-scoped diff and no unrelated orphan
changes. On FAIL/PARTIAL: no commit, no tag; write a blocker report and stop.

## Safety constraints

Do not read .env. Do not print or infer secrets. Do not use .env model names.
Do not use real customer data. Do not mutate the production/default vectorstore.
Do not run Docker. Do not deploy externally. Do not push remotely. Do not change
product runtime behavior unless this prompt's scope explicitly and safely
requires it with tests. Do not weaken tenant authorization, tenant isolation,
API key behavior, OIDC/session behavior, RBAC behavior, rate limiting, or
production_safe behavior. Do not change retrieval thresholds or cross-encoder
settings unless explicitly analyzed, justified, and tested. Do not expose API
keys, OIDC secrets, session secrets, trust tokens, raw prompts, raw document
text, tenant-private data, or customer-private data in reports, docs, tests,
prompts, metrics, alerts, or artifacts. No new dependencies unless explicitly
justified. Leave unrelated orphan files untouched (including
docs/reports/japan_rag_market_positioning_after_prompt030.md and
prompts/claude/market/). Preserve all completed behavior from Prompts034-054.

## Conservative no-overclaim requirement

Be strict and evidence-based. Do not claim production readiness, accuracy
guarantees, HA, 24x7 SLA, compliance certification, or competitor superiority.
Separate mock-tested / synthetic-data evidence from anything that requires a real
customer environment, real IdP tenant, or real documents, and label each clearly.

## Commit/tag policy

PASS -> commit message "prompt056 safe collection promotion workflow", tag "prompt056-safe-collection-promotion-workflow".
PARTIAL/FAIL -> no commit, no tag; report the blocker and the next command.

## Required final output

1. Preconditions  2. Implementation/analysis summary  3. Safety / no-secret /
no-customer-data result  4. Verification results (targeted first; state if full
suite not run)  5. Deliverable paths  6. Git diff summary  7. Commit/tag result
8. Final judgment PASS/PARTIAL/FAIL  9. Next recommendation
