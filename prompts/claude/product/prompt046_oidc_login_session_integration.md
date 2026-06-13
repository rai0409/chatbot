# Prompt046: OIDC Login / Session Integration

You are working in:

/home/rai/chatbot
## Goal

Implement a DEFAULT-OFF OIDC (OAuth2 Authorization Code + PKCE) login/session
path, only if Prompt045 confirmed it. Use a safe, widely-used library for
state/nonce/PKCE and JWKS/ID-token verification - do NOT write homegrown crypto.
The existing API key path and the Prompt037 enterprise bridge must remain
working and unchanged when OIDC is disabled.

## Scope

- Config-gated (ENTERPRISE_OIDC_ENABLED default false). When off, zero behavior
  change and no new request surface honored.
- Login/callback endpoints, secure server-side session (signed/httponly cookies;
  secret env-only, never logged), state+nonce+PKCE, JWKS token verification.
- The OIDC session yields an identity + claims; map to the existing
  ApiAuthContext-style tenant authorization (full group->tenant/role mapping is
  Prompt047). Fail closed on invalid/missing token.
- If a dependency is added (e.g. authlib), pin minimally and justify per
  Prompt045; update requirements.

## Tests (tests/test_oidc_login_session.py)

Prove with a MOCK IdP / mock JWKS and synthetic tokens: default-off changes
nothing and API key path still works; invalid/missing token fails closed; valid
token establishes a session mapped to the right tenant; no token/secret/cookie
value leaked in responses, logs, or metrics.

## Verification

    python -m pytest tests/test_oidc_login_session.py tests/test_api_auth.py tests/test_enterprise_auth_bridge.py -q
    python -m pytest -q
    scripts/limited_beta_preflight.sh

## Report

docs/reports/prompt046_oidc_login_session_integration.md


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

PASS -> commit message "prompt046 oidc login session integration", tag "prompt046-oidc-login-session-integration".
PARTIAL/FAIL -> no commit, no tag; report blocker and the next command.

## Required final output

1. Preconditions  2. Implementation summary  3. Safety/no-secret-exposure result
4. Verification results (targeted first; state if full suite not run)
5. Docs/report path  6. Git diff summary  7. Commit/tag result
8. Final judgment PASS/PARTIAL/FAIL  9. Next recommendation
