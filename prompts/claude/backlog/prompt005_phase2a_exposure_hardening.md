# Prompt005: Phase 2-A Exposure Hardening

You are working in:

/home/rai/chatbot

## Goal

Implement Phase 2-A: exposure hardening only.

Protect public-facing endpoints from unauthenticated commercial exposure and lock down debug surfaces.

Focus on API authentication, rate limiting, CORS allowlisting, and /search/debug gating.

## Execution mode

Proceed autonomously.

Do not ask for human confirmation for ordinary local edits, targeted tests, smoke checks, or local verification.

Stop only if one of the following occurs:

- A destructive operation would be required.
- User data would be deleted.
- .env, secrets, tokens, API keys, or private credentials would need to be read, printed, changed, or inferred.
- A remote push, force push, or remote deployment would be required.
- Existing auth/admin configuration cannot be safely understood.
- The target files cannot be found and the correct location is ambiguous.
- Verification fails in a way that cannot be safely classified after one bounded fix attempt.

If verification fails because of your changes, perform one bounded fix attempt, rerun targeted verification, and report final status.

## Preconditions

Before implementing, check whether Prompt001 to Prompt004 appear complete.

If any required earlier retrieval integrity or no-answer guard work is absent, do not implement exposure hardening. Instead, write the appropriate fix prompt to prompts/claude/ and stop.

## Scope

Implement only the following:

1. Add or extend API-key authentication middleware or dependencies for public endpoints.

Protect at minimum:

- /chat
- /search
- /search/debug

2. Gate /search/debug behind admin auth or an explicit non-production debug setting.

3. Add simple in-process rate limiting if no existing rate limit mechanism exists.

Use existing configuration patterns. Do not add external services.

4. Add CORS allowlist configuration if CORS is currently missing.

5. Add /health behavior that remains accessible without secrets.

6. Add tests only for:

- unauthenticated /chat is rejected when auth is enabled
- authenticated /chat is accepted when auth is enabled
- /search/debug is rejected without admin/debug permission
- /health remains accessible
- rate limit blocks excessive requests when enabled
- CORS allowlist behavior if CORS is implemented

## Explicit non-goals

Do not implement these in this prompt:

- tenant isolation
- user account system
- OAuth
- database-backed rate limiting
- Redis
- deployment changes
- Docker/CI changes
- streaming
- RAG scoring changes
- citation validation changes
- broad refactors
- unrelated formatting changes

## Constraints

- No new dependencies unless an existing dependency already supports the required behavior.
- Do not read or print .env.
- Do not expose secrets.
- Do not hardcode real API keys.
- Use placeholder config names only.
- Preserve local development usability through explicit config defaults.
- Keep changes minimal and localized.
- Do not run full test suites unless targeted verification clearly requires it.

## Verification

Run targeted tests first.

Then run:

python -m pytest --collect-only

If available and safe, run:

scripts/product_readiness_smoke.sh

Do not run broad, slow, or external-network-dependent tests unless necessary.

## Required final output

Report in this exact order:

1. Preconditions

Include:

- repo path
- branch
- initial git status summary
- Prompt001 to Prompt004 readiness check
- relevant files found

2. Implementation summary

Include:

- files changed
- exact exposure hardening behavior added
- config names added or reused
- explicit non-goals preserved

3. Verification results

Include:

- targeted tests
- pytest collect-only
- product readiness smoke, if run
- any skipped verification and why

4. Git diff summary

Include:

- git diff --stat
- do not paste large diffs

5. Final judgment

Use one of:

- PASS
- PARTIAL
- FAIL

Also state whether this is safe to continue to the next phase.

6. Next prompt file

If final judgment is PASS, write exactly one next recommended prompt to:

prompts/claude/prompt006_phase1b_citation_integrity.md

The next prompt should cover citation integrity and fallback metrics only.

Do not execute Prompt006 in this run.
