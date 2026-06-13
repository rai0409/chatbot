# Prompt048: Entra/Okta/Reverse-Proxy/TLS Deployment Guides

You are working in:

/home/rai/chatbot
## Goal

Create production deployment guides (docs only; no runtime change) for connecting
KuraDen to real IdPs and proxies: Entra ID / Microsoft, Okta, generic OIDC,
reverse-proxy/gateway (header stripping + trusted-header bridge), and TLS
termination. Be explicit about what is locally tested vs what requires a real
customer IdP tenant.

## Scope

- Per-IdP setup: client/app registration, redirect URIs, scopes, group claims;
  mapping to ENTERPRISE_OIDC_* / ENTERPRISE_AUTH_* config (placeholders only).
- Reverse-proxy recipes (nginx / oauth2-proxy) that authenticate and STRIP
  client-supplied identity headers before injecting trusted ones.
- TLS termination reference; audit logging guidance; a "locally tested vs
  requires customer IdP tenant" matrix.

## Tests

Docs-only; optionally a presence/lint test. No runtime change.

## Verification

    git status --short
    python -m pytest --collect-only -q

## Report

docs/reports/prompt048_entra_okta_reverse_proxy_tls_deployment_guides.md


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

PASS -> commit message "prompt048 entra okta reverse proxy tls deployment guides", tag "prompt048-entra-okta-reverse-proxy-tls-deployment-guides".
PARTIAL/FAIL -> no commit, no tag; report blocker and the next command.

## Required final output

1. Preconditions  2. Implementation summary  3. Safety/no-secret-exposure result
4. Verification results (targeted first; state if full suite not run)
5. Docs/report path  6. Git diff summary  7. Commit/tag result
8. Final judgment PASS/PARTIAL/FAIL  9. Next recommendation
