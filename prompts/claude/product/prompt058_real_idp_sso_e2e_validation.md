# Prompt058: Real-IdP SSO End-to-End Validation Workflow

You are working in:

/home/rai/chatbot
## Context

In-app OIDC (Prompt046) + group->tenant RBAC (Prompt047) are implemented and
MOCK-tested (synthetic RSA/JWKS/tokens). End-to-end validation against a real
Entra ID / Okta tenant is unproven and requires the customer's IdP. No real
credentials may be used here.

## Goal

Create a real-IdP SSO validation WORKFLOW for Entra ID and Okta over the existing
OIDC path: a customer-IT checklist, redirect-URI/TLS/cookie requirements, group-
claim->tenant/role mapping steps, failure handling, and a clear separation of
mock-tested vs real-tenant-tested evidence. Do not use real credentials.

## Scope

- A step-by-step validation checklist per IdP (app registration, redirect URI,
  scopes, group claims) referencing the existing ENTERPRISE_OIDC_* / RBAC config
  and docs/reports/prompt048 guides.
- A mock-tested-vs-real-tenant evidence matrix and a sign-off template.
- Failure-handling guidance (invalid token, clock skew, unmapped group, cookie
  Secure behind TLS).

## Required deliverables

- docs/operations/ checklist + docs/reports/prompt058_real_idp_sso_e2e_validation.md.
- Optional: a local mock-IdP smoke note reusing tests/test_oidc_login_session
  patterns (no real IdP). Docs-first; minimal/no runtime change.

## Tests / checks

    python -m pytest tests/test_oidc_login_session.py tests/test_group_tenant_rbac.py -q
    python -m pytest --collect-only -q


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

PASS -> commit message "prompt058 real idp sso e2e validation", tag "prompt058-real-idp-sso-e2e-validation".
PARTIAL/FAIL -> no commit, no tag; report the blocker and the next command.

## Required final output

1. Preconditions  2. Implementation/analysis summary  3. Safety / no-secret /
no-customer-data result  4. Verification results (targeted first; state if full
suite not run)  5. Deliverable paths  6. Git diff summary  7. Commit/tag result
8. Final judgment PASS/PARTIAL/FAIL  9. Next recommendation
