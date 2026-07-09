# Prompt042: Conversation History & Thread Persistence

You are working in:

/home/rai/chatbot
## Goal

Add safe conversation history / thread persistence behind a small, default-off
or clearly-bounded local persistence boundary with strict tenant/user isolation
and retention controls. If no suitable storage layer exists, implement the
smallest local boundary (e.g. a per-tenant/user JSONL or sqlite store under
RUNS_DIR) - do NOT reuse the vector store for chat history. Store no secrets and
no raw API keys; never broaden tenant access.

## Scope

- A persistence module with create/list/get/delete-by-retention, keyed by
  (tenant_id, user/identity fingerprint, thread_id). Isolation: a tenant/user
  must never read another tenant/user's threads.
- Optional endpoints to list/load/delete the caller's own threads, gated by the
  existing auth + tenant authorization (no new auth scheme); default-off if it
  adds runtime surface.
- Sidebar wiring in the Prompt041 workspace to show the caller's own history.
- Retention: a max-age / max-count cap and a documented purge path.

## Tests (tests/test_conversation_history.py)

Prove: write/read round-trip; tenant/user isolation (cross-tenant/user read
denied); retention purge; no secret/API key/raw prompt leakage beyond stored
message text scoped to the owner; existing auth/tenant tests still pass.

## Verification

    python -m pytest tests/test_conversation_history.py tests/test_tenant_isolation.py tests/test_api_key_tenant_authorization.py -q
    python -m pytest -q
    scripts/limited_beta_preflight.sh

## Report

docs/reports/prompt042_conversation_history_and_thread_persistence.md


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

PASS -> commit message "prompt042 conversation history and thread persistence", tag "prompt042-conversation-history-and-thread-persistence".
PARTIAL/FAIL -> no commit, no tag; report blocker and the next command.

## Required final output

1. Preconditions  2. Implementation summary  3. Safety/no-secret-exposure result
4. Verification results (targeted first; state if full suite not run)
5. Docs/report path  6. Git diff summary  7. Commit/tag result
8. Final judgment PASS/PARTIAL/FAIL  9. Next recommendation
