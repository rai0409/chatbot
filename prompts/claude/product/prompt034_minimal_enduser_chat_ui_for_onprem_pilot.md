# Prompt034: Minimal End-User Chat UI for On-Prem Manufacturing Pilot

You are working in:

/home/rai/chatbot

## Goal

Add a minimal, brandable end-user chat UI that a non-technical employee can use
in an on-prem manufacturing pilot, served by the existing backend. This is the
#1 commercial gap identified in the Prompt032 web research and the Prompt033
roadmap: across every verified Japanese competitor a usable end-user UI is
table-stakes, while our core RAG/answer workflow is already in place.

The UI must reuse the existing endpoints and must not change retrieval,
answering, auth, tenant, guard, or rate-limit behavior. It is a thin client
plus one static-serving route.

## Execution mode

Proceed autonomously.

Commit and tag automatically only if this prompt reaches PASS and the git diff
is limited to this prompt scope (the UI asset, its serving route, tests, docs,
and this prompt's report).

Stop only for: destructive operations, user-data deletion, secrets/.env access,
remote push/deploy, production/default vectorstore mutation, required
network/model downloads, ambiguous missing targets, unsafe request-path
behavior, or unresolved verification failure after one bounded fix attempt.

Do not read .env.
Do not print or infer secrets.
Do not download models.
Do not run Prompt020.
Do not change cross-encoder settings.
Do not change distance thresholds.
Do not change tenant authorization semantics.
Do not change tenant isolation semantics.
Do not change rate-limiter semantics.
Do not change the too_general guard.
Do not change the production_safe profile behavior.
Do not mutate the production/default vectorstore or default collection.
Do not use real customer data.
Do not push remotely.
Do not deploy externally.
No new dependencies (vanilla HTML/CSS/JS only; no build step, no frontend
framework packages).

## Preconditions to verify

Verify and record:

- Current branch and HEAD.
- Working tree has no unexpected tracked diff.
- Tag analysis-commercial-product-development-roadmap-after-prompt032 exists.
- Tag prompt030-durable-multitenant-persistence-verification exists.
- webapi/main.py exposes POST /chat/stream (SSE) and POST /chat/feedback, both
  behind require_api_auth_rate_limited, and a static dir webapi/static/ with
  existing pages (product_preview.html, review_queue.html) served via routes.
- The SSE event names emitted by /chat/stream (for example approved, final,
  error, and any token/meta events) so the client parses the real contract.
- The /chat/feedback request shape (feedback_token, feedback_type, tenant_id).

## Required design

### 1. Static UI asset

Add a single self-contained page, for example
webapi/static/chat.html, using only vanilla HTML/CSS/JS (no external CDN, no
new dependency, no build). It must:

- present a question input and a conversation view;
- call POST /chat/stream and render the streamed answer;
- render citations/sources when present in the response;
- when the system abstains or returns no answer (guard or no-answer), show a
  clear, calm message (for example shows that the system could not find a
  supported answer) rather than fabricating content;
- offer a simple feedback control (good / bad / request human review) that
  calls POST /chat/feedback with the returned feedback token;
- never embed or echo a raw API key in page source, inline script, logs, or
  network error text rendered to the DOM.

### 2. Serving route

Add one GET route (for example GET /chat-ui) in webapi/main.py that serves the
static page, mirroring how the existing product-preview page is served. Keep
/health and /metrics unaffected.

### 3. Access gating (default-off, within existing auth)

- Do NOT invent a new auth scheme. Stay within the existing API_AUTH_* model.
- When API auth is enabled, the page must obtain the key from the user at
  runtime (for example a field the operator pastes the pilot key into, kept in
  browser memory/session only) and send it as the existing header; it must not
  be hardcoded in the served HTML.
- Default behavior with API auth disabled must be unchanged (page works locally
  without a key, exactly as /chat does today).
- The serving route itself must not leak keys and must not bypass
  enforce_tenant_authorization on the data endpoints.

### 4. Observability/audit safety

If anything is logged server-side for the new route, include only safe values
(route, status). No raw keys, query text in metrics, document content, or
secrets.

### 5. Tests

Prefer a new test file, for example
tests/test_enduser_chat_ui.py, proving:

- GET /chat-ui returns 200 and HTML containing the chat UI markup;
- the served HTML contains no hardcoded API key and no secret-like token;
- with API auth enabled, the data endpoints still return 401 without a key and
  403 for an unauthorized tenant (UI route may still serve the page, but data
  calls remain protected) and the pipeline is not invoked on rejection;
- production_safe behavior is unchanged (no new free-form generation enabled by
  the UI path);
- /health and /metrics remain unaffected;
- no raw API key appears in any test-captured response body, headers, or logs.

### 6. Documentation

Update docs minimally:

- a short note in docs/operations.md or the pilot onboarding runbook describing
  the end-user UI route, that it reuses /chat/stream and /chat/feedback, that it
  honors production_safe, and that access uses the existing API key model;
- make clear the UI does not change answering, guard, tenant, or rate-limit
  semantics.

### 7. Analysis artifact

Add a short implementation report:

- docs/reports/prompt034_minimal_enduser_chat_ui_for_onprem_pilot.md

It must include: what was added, what remained unchanged, default-off safety,
no-secret-exposure evidence, test evidence, and remaining gaps (SSO, admin
console, mobile).

## Explicit non-goals

- SSO/AD integration (separate prompt).
- Admin/management console.
- Multi-tenant billing or self-serve signup.
- Mobile app.
- Any change to retrieval ranking, guard, cross-encoder, distance thresholds,
  tenant authorization/isolation, or rate-limiter semantics.
- New dependencies or a frontend build pipeline.

## Verification

Run these targeted checks first:

    python -m pytest tests/test_enduser_chat_ui.py -q
    python -m pytest tests/test_chat_stream.py tests/test_api_auth.py tests/test_api_key_tenant_authorization.py tests/test_rate_limit.py -q

Then run broader safety checks:

    python -m pytest --collect-only -q
    python -m pytest -q
    scripts/product_readiness_smoke.sh
    scripts/limited_beta_preflight.sh

Then run synthetic evals:

    PYTHONPATH=. .venv/bin/python -m eval.runner --cases eval/cases/smoke_cases.jsonl --chunks-jsonl eval/cases/smoke_chunks.jsonl --output runs/eval/prompt034_smoke_check.json
    PYTHONPATH=. .venv/bin/python -m eval.runner --cases eval/cases/qa_pair_cases.jsonl --chunks-jsonl eval/cases/qa_pair_chunks.jsonl --output runs/eval/prompt034_qa_pair_check.json

Optional only if Docker is available and safe (document, do not run
automatically):

    scripts/limited_beta_preflight.sh --with-docker-smoke

Do not run commands that read .env.
Do not mutate the production/default vectorstore.

## Commit/tag policy

PASS:

- commit message: prompt034 minimal enduser chat ui for onprem pilot
- tag: prompt034-minimal-enduser-chat-ui-for-onprem-pilot

PARTIAL or FAIL:

- no commit
- no tag
- report blocker and next command

## Required final output

1. Preconditions
2. Implementation summary
3. Default-off / no-secret-exposure safety result
4. UI serving and endpoint-wiring result
5. Verification results
6. Docs/report paths
7. Git diff summary
8. Commit/tag result
9. Final judgment: PASS / PARTIAL / FAIL
10. Next recommendation
