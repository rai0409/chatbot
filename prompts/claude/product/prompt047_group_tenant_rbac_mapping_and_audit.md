# Prompt047: Group->Tenant RBAC Mapping & Audit

You are working in:

/home/rai/chatbot
## Goal

Implement group-to-tenant mapping and role-based access control on top of the
OIDC (Prompt046) and/or enterprise-bridge (Prompt037) identity, with cross-tenant
rejection, audit-safe identity fingerprints, and role enforcement. Never expose
raw identity or group values.

## Scope

- A mapping from IdP groups/claims to allowed tenants + role (admin/operator/
  user/viewer), reusing the existing fail-closed tenant-authorization approach;
  identity never broadens the request tenant_id.
- Audit records use sha256-derived fingerprints only (never raw email/group);
  stable enum counters only in metrics.
- Role enforcement server-side for privileged actions (ties to Prompt043).

## Tests (tests/test_group_tenant_rbac.py)

Prove: group->tenant mapping resolves allowed tenants; cross-tenant access
rejected; unmapped identity fails closed; role gates privileged actions; audit/
metrics contain only fingerprints/enums (no raw identity, group, or secret);
existing tenant isolation/authorization tests pass.

## Verification

    python -m pytest tests/test_group_tenant_rbac.py tests/test_tenant_isolation.py tests/test_api_key_tenant_authorization.py -q
    python -m pytest -q

## Report

docs/reports/prompt047_group_tenant_rbac_mapping_and_audit.md


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

PASS -> commit message "prompt047 group tenant rbac mapping and audit", tag "prompt047-group-tenant-rbac-mapping-and-audit".
PARTIAL/FAIL -> no commit, no tag; report blocker and the next command.

## Required final output

1. Preconditions  2. Implementation summary  3. Safety/no-secret-exposure result
4. Verification results (targeted first; state if full suite not run)
5. Docs/report path  6. Git diff summary  7. Commit/tag result
8. Final judgment PASS/PARTIAL/FAIL  9. Next recommendation
