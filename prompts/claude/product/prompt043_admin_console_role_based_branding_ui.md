# Prompt043: Admin Console, Role-Aware UI & Branding

You are working in:

/home/rai/chatbot
## Goal

Add admin console foundations and a role-aware UI (admin / operator / user /
viewer), plus safe customer branding (logo text/theme via server-provided
config). Backend enforcement must NOT rely on frontend-only checks: any
privileged route/data must be enforced server-side (reuse admin_auth / api_auth;
do not weaken them).

## Scope

- Role-aware rendering in the workspace; privileged panels (admin/operator) only
  shown when the backend confirms the role. The existing /admin/review surface
  is reused; new admin views must be backend-enforced.
- Branding config: a safe, secret-free server endpoint or static config for
  logo text / product name / theme color; no secrets, no per-tenant private data.
- Document the role model and that frontend gating is cosmetic; the server is
  authoritative.

## Tests (tests/test_admin_console_roles_branding.py)

Prove: privileged routes/data require backend role/admin auth (frontend cannot
bypass); branding config exposes no secret; unauthenticated/underprivileged
access is rejected server-side; existing admin_auth tests still pass.

## Verification

    python -m pytest tests/test_admin_console_roles_branding.py tests/test_admin_auth.py -q
    python -m pytest -q
    scripts/product_readiness_smoke.sh

## Report

docs/reports/prompt043_admin_console_role_based_branding_ui.md


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

PASS -> commit message "prompt043 admin console role based branding ui", tag "prompt043-admin-console-role-based-branding-ui".
PARTIAL/FAIL -> no commit, no tag; report blocker and the next command.

## Required final output

1. Preconditions  2. Implementation summary  3. Safety/no-secret-exposure result
4. Verification results (targeted first; state if full suite not run)
5. Docs/report path  6. Git diff summary  7. Commit/tag result
8. Final judgment PASS/PARTIAL/FAIL  9. Next recommendation
