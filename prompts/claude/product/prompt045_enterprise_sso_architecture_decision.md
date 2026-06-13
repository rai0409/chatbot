# Prompt045: Enterprise SSO Architecture Decision

You are working in:

/home/rai/chatbot
## Goal

Analysis/decision prompt (no runtime change unless strictly necessary). Decide
the exact SSO path for KuraDen. Compare OIDC, SAML, LDAP, Entra ID, Okta, and
reverse-proxy/gateway against the existing auth model (api_auth +
enterprise_auth trusted-proxy bridge) and the commercial goal.

## Scope

- Dependency analysis: current deps have no OIDC/SAML library. Recommend the
  smallest widely-used library only if a robust in-app path requires it
  (authlib is the candidate for OIDC). No homegrown crypto.
- Decision: primary in-app path = OIDC/OAuth2 Authorization Code + PKCE
  (default-off), unless repo evidence says otherwise; SAML/LDAP via
  proxy/gateway (reusing Prompt037). State exactly what "SSO-connected" means,
  what is locally implementable+testable (mock IdP/JWKS), and what requires a
  real customer IdP tenant.
- Output: a decision report and the precise implementation contract for
  Prompt046 (endpoints, session/cookie strategy, token verification, identity to
  tenant/role mapping, fail-closed behavior).

## Tests

None required (analysis). If any helper is added, add a minimal test.

## Verification

    git status --short
    python -m pytest --collect-only -q

## Report

docs/reports/prompt045_enterprise_sso_architecture_decision.md


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

PASS -> commit message "prompt045 enterprise sso architecture decision", tag "prompt045-enterprise-sso-architecture-decision".
PARTIAL/FAIL -> no commit, no tag; report blocker and the next command.

## Required final output

1. Preconditions  2. Implementation summary  3. Safety/no-secret-exposure result
4. Verification results (targeted first; state if full suite not run)
5. Docs/report path  6. Git diff summary  7. Commit/tag result
8. Final judgment PASS/PARTIAL/FAIL  9. Next recommendation
