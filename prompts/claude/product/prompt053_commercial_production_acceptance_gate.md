# Prompt053: Commercial Production Acceptance Gate

You are working in:

/home/rai/chatbot
## Goal

Run a production acceptance gate across UI, SSO, operations, security, tests,
docs, and the commercial claim boundary. Recompute the five readiness judgments
from repo evidence only. Analysis/report prompt (no runtime change).

## Scope

- Verify each prior stage (041-052) is committed/tagged and its tests pass
  (targeted; full suite if reasonable, else say so).
- Recompute: internal demo / manufacturing one-department PoC / limited external
  beta / first paid annual contract / general production - each READY /
  READY WITH CONDITIONS / PARTIAL / NOT READY, with evidence.
- Restate the safe-to-claim vs not-safe-to-claim boundary after the upgrade.

## Tests

Run targeted suites for each pillar; report results honestly; do not fabricate.

## Verification

    git tag --list | grep -E 'prompt04[1-9]|prompt05[0-2]'
    python -m pytest --collect-only -q
    python -m pytest -q
    scripts/product_readiness_smoke.sh
    scripts/limited_beta_preflight.sh

## Report

docs/reports/prompt053_commercial_production_acceptance_gate.md


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

PASS -> commit message "prompt053 commercial production acceptance gate", tag "prompt053-commercial-production-acceptance-gate".
PARTIAL/FAIL -> no commit, no tag; report blocker and the next command.

## Required final output

1. Preconditions  2. Implementation summary  3. Safety/no-secret-exposure result
4. Verification results (targeted first; state if full suite not run)
5. Docs/report path  6. Git diff summary  7. Commit/tag result
8. Final judgment PASS/PARTIAL/FAIL  9. Next recommendation
