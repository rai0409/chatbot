# Prompt006: Phase 2-A API Exposure Hardening

You are working in:

/home/rai/chatbot

## Goal

Implement Phase 2-A: API exposure hardening only — opt-in API-key auth for public endpoints, gating /search/debug, and an explicit CORS allowlist.

Today, /chat, /search, /search/debug, /chat/product-preview, and /chat/feedback have no authentication, and /search/debug exposes retrieval internals (chunk previews, rerank traces, score details) to any caller. Admin routes already have optional token auth (webapi/admin_auth.py).

## Execution mode

Proceed autonomously.

Do not ask for human confirmation for ordinary local edits, targeted tests, smoke checks, or local verification.

Stop only if one of the following occurs:

- A destructive operation would be required.
- User data would be deleted.
- .env, secrets, tokens, API keys, or private credentials would need to be read, printed, changed, or inferred.
- A remote push, force push, or remote deployment would be required.
- The target files cannot be found and the correct location is ambiguous.
- Verification fails in a way that cannot be safely classified after one bounded fix attempt.

If verification fails because of your changes, perform one bounded fix attempt, rerun the targeted verification, and report final status.

## Scope

Implement only the following:

1. API-key auth for non-admin endpoints, mirroring the admin_auth.py pattern (opt-in via env, constant-time comparison, never logging the token value):

- API_AUTH_ENABLED (default false: current behavior unchanged)
- API_AUTH_KEYS (comma-separated accepted keys; reject all requests with 503 "api auth is not configured" if enabled but empty)
- Accept the key via X-Api-Key header or Authorization: Bearer.
- Protect: /chat, /search, /search/debug, /chat/product-preview, /chat/feedback.
- Leave /health unauthenticated.
- Admin routes keep their existing separate admin auth.

2. Gate /search/debug independently:

- SEARCH_DEBUG_ENABLED (default true for backward compatibility).
- When false, /search/debug returns 404.
- When API auth is enabled, /search/debug additionally requires admin auth (it exposes internals), not just an API key.

3. CORS allowlist:

- CORS_ALLOW_ORIGINS (comma-separated; default empty = no CORS middleware added, preserving current same-origin behavior).
- When set, add FastAPI's CORSMiddleware with exactly those origins, no wildcard credentials.

4. Add targeted tests only for:

- auth disabled: all endpoints behave as before (no auth required)
- auth enabled without key: 401; with wrong key: 403; with correct key (either header form): success path reachable
- auth enabled but no keys configured: 503
- SEARCH_DEBUG_ENABLED=false: /search/debug returns 404
- /health stays open with auth enabled
- token values never appear in logs or error responses (assert on response bodies)

## Explicit non-goals

Do not implement these in this prompt:

- rate limiting
- new dependencies (FastAPI's built-in CORSMiddleware only)
- TLS, reverse-proxy, or deployment config
- tenant isolation
- changing response payloads of any endpoint
- streaming, caching
- broad refactors

## Constraints

- No new dependencies.
- Do not read or print .env.
- Do not expose secrets; never echo provided or expected keys in responses or logs.
- Default behavior with no new env vars set must be byte-identical to today.
- Tests must not require network access or an OpenAI API key.
- Keep changes minimal and localized (a small webapi/api_auth.py next to admin_auth.py is the expected shape).
- Do not run full test suites unless targeted verification clearly requires it.

## Verification

Run targeted tests first.

Then run:

python -m pytest --collect-only

If available and safe, run scripts/product_readiness_smoke.sh.

Also update docs/production_readiness_checklist.md with a short "API Auth" section listing the new env vars and their defaults (documentation only).

## Required final output

Report in this exact order:

1. Preconditions (repo path, branch, initial git status summary, relevant files found; verify Prompt005 is complete — guard fallbacks return empty citations — before implementing)
2. Implementation summary (files changed, exact behavior added, explicit non-goals preserved)
3. Verification results (targeted tests, collect-only, smoke if run, any skipped verification and why)
4. Git diff summary (git diff --stat, no large diffs)
5. Final judgment: PASS / PARTIAL / FAIL, and whether it is safe to continue to Prompt007.
6. Next prompt file: if PASS, write exactly one next recommended prompt to prompts/claude/prompt007_phase2b_llm_call_hardening.md covering LLM call hardening only (request timeout, bounded retries with backoff for the chat completion call, max_tokens bound, and reusing the existing _generation_error_payload classification in /chat instead of generic 500s). Do not execute Prompt007 in this run.
