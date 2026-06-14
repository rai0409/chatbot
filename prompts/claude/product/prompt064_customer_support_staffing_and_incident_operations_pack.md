# Prompt064: Customer Support Staffing & Incident Operations Pack

You are working in:

/home/rai/chatbot
## Context

The SLO/SLA + incident runbook (Prompt052) is honest about no 24x7. An annual
contract needs a support-operations package; do not claim 24x7 unless marked
future/optional.

## Goal

Create a support operations package: severity definitions, escalation matrix,
response-time targets, staffing assumptions, customer communication templates,
runbook links, and an incident-review template.

## Scope / deliverables

- docs/operations/ support pack referencing prompt052 + rollback runbook.
- 24x7 only as a clearly-marked FUTURE/OPTIONAL tier; default = business hours.
- docs/reports/prompt064_customer_support_staffing_and_incident_operations_pack.md.
- Placeholders only; no real names/secrets.

## Tests / checks

    git status --short ; python -m pytest --collect-only -q


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

PASS -> commit message "prompt064 customer support staffing and incident operations pack", tag "prompt064-customer-support-staffing-and-incident-operations-pack".
PARTIAL/FAIL -> no commit, no tag; report the blocker and the next command.

## Required final output

1. Preconditions  2. Implementation/analysis summary  3. Safety / no-secret /
no-customer-data result  4. Verification results (targeted first; state if full
suite not run)  5. Deliverable paths  6. Git diff summary  7. Commit/tag result
8. Final judgment PASS/PARTIAL/FAIL  9. Next recommendation
