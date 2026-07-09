# Prompt041: Commercial Chat Workspace UI

You are working in:

/home/rai/chatbot
## Goal

Upgrade the minimal end-user page (webapi/static/chat.html, served at GET
/chat-ui) into a commercial-grade, ChatGPT/Claude-style chat workspace that
reuses the EXISTING backend APIs (/chat/stream SSE, /chat/feedback) without
changing any backend or auth semantics. Vanilla HTML/CSS/JS only (no build step,
no new dependency) unless a build step is explicitly justified here.

## Scope

- A workspace layout: left sidebar (placeholder for conversation list, wired in
  Prompt042), main chat column, and a right-hand citations/sources panel.
- Render streamed answers, citations, a calm abstain/no-answer state, and
  feedback controls (good / bad / human-review) exactly as the current client
  contract (per-question UUID used as trace_id + feedback_token).
- Runtime API key + tenant fields (no hardcoded key); generic key-free error
  text; defensive HTML escaping.
- Keep GET /chat-ui serving a static shell; do not add new backend endpoints
  beyond what is needed to serve assets. /health and /metrics unchanged.

## Tests (tests/test_commercial_chat_workspace_ui.py)

Prove: /chat-ui serves 200 HTML with the workspace markup (sidebar, citations
panel, composer); wired to /chat/stream and /chat/feedback; no hardcoded API key
/ secret-like token in the page; with API auth enabled the data endpoints stay
protected (401 no key, 403 wrong tenant) and the pipeline is not invoked on
rejection; production_safe unchanged; /health and /metrics unaffected.

## Verification

    python -m pytest tests/test_commercial_chat_workspace_ui.py tests/test_enduser_chat_ui.py -q
    python -m pytest --collect-only -q
    python -m pytest -q
    scripts/product_readiness_smoke.sh
    scripts/limited_beta_preflight.sh

## Report

docs/reports/prompt041_commercial_chat_workspace_ui.md


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

PASS -> commit message "prompt041 commercial chat workspace ui", tag "prompt041-commercial-chat-workspace-ui".
PARTIAL/FAIL -> no commit, no tag; report blocker and the next command.

## Required final output

1. Preconditions  2. Implementation summary  3. Safety/no-secret-exposure result
4. Verification results (targeted first; state if full suite not run)
5. Docs/report path  6. Git diff summary  7. Commit/tag result
8. Final judgment PASS/PARTIAL/FAIL  9. Next recommendation
