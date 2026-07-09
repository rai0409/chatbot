# Prompt044: Document Ingestion UI & Job Status

You are working in:

/home/rai/chatbot
## Goal

Add a document ingestion UI and a job/status view, reusing the existing dry-run
onboarding + import manifest paths (scripts/onboard_documents_dry_run.py,
scripts/import_manifest.py). Use staging / explicit NON-production collection
semantics only; never allow direct unsafe production/default vectorstore
mutation from the UI.

## Scope

- Admin/operator-gated UI to: upload/select synthetic documents, run a dry-run
  onboarding into an explicit non-production collection, and view the import
  manifest result (duplicate ids / tenant mismatch / collisions) + job status.
- Backend endpoints (admin/operator enforced) that wrap the existing dry-run /
  manifest logic; production/default collection is refused (as onboarding
  already enforces). No new vectorstore mutation path that bypasses guards.

## Tests (tests/test_ingestion_ui_job_status.py)

Prove: ingestion endpoints require admin/operator; production/default collection
is refused; dry-run produces a manifest with the expected issue checks on
synthetic data; job status reflects result; no secret/raw-doc leakage; existing
onboarding tests pass.

## Verification

    python -m pytest tests/test_ingestion_ui_job_status.py tests/test_multiformat_onboarding.py -q
    python -m pytest -q
    scripts/limited_beta_preflight.sh

## Report

docs/reports/prompt044_document_ingestion_ui_and_job_status.md


## Global safety constraints (apply to this prompt)

Do not read .env. Do not print or infer secrets. Do not use .env model names.
Do not use real customer data. Do not mutate the production/default vectorstore
or default collection except through an explicitly safe, tested staged workflow.
Do not run Docker (unless this prompt explicitly decides it is safe and necessary
for local-only validation). Do not deploy externally. Do not push remotely.
Do not weaken tenant authorization, tenant isolation, API key behavior, rate
limiting, or production_safe behavior. Do not change retrieval thresholds or
cross-encoder settings unless this prompt explicitly analyzes and justifies it
with tests. Do not expose API keys, SSO secrets, trust tokens, raw prompts, raw
document text, or tenant-private data in UI, logs, metrics, alerts, reports, or
tests. No new dependencies unless explicitly justified by this prompt. Leave
unrelated orphan files untouched (including previous market prompt/report
orphans). Preserve Prompt034 UI, Prompt035 Chroma where, Prompt036 monitoring,
and Prompt037 enterprise-auth behavior unless explicitly in this prompt's scope.

## Execution mode

Proceed autonomously. Run targeted tests first; run broader tests only when
targeted tests pass and runtime is reasonable; never fabricate test results; if
the full suite is not run, say so. Commit and tag only on PASS with a
prompt-scoped diff and no unrelated orphan changes. On FAIL/PARTIAL: no commit,
no tag; write a blocker report and stop.

## Commit/tag policy

PASS -> commit message "prompt044 document ingestion ui and job status", tag "prompt044-document-ingestion-ui-and-job-status".
PARTIAL/FAIL -> no commit, no tag; report blocker and the next command.

## Required final output

1. Preconditions  2. Implementation summary  3. Safety/no-secret-exposure result
4. Verification results (targeted first; state if full suite not run)
5. Docs/report path  6. Git diff summary  7. Commit/tag result
8. Final judgment PASS/PARTIAL/FAIL  9. Next recommendation
