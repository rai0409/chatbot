# Prompt005: Phase 2-A Q&A Exposure Hardening

You are working in:

/home/rai/chatbot

## Goal

Implement Phase 2-A for Q&A chatbot usage: protect public-facing Q&A endpoints from unauthenticated commercial exposure and lock down debug surfaces.

Focus on minimal API authentication, rate limiting, CORS allowlisting, and /search/debug gating.

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

Required signs:

- keyword index health fields exist
- embedding provider/model mismatch is visible or blocked
- duplicate Q&A retrieval work has been reduced or clearly measured
- Q&A no-answer guard uses real retrieval evidence or no longer relies on fabricated pseudo-distance

If these signs are absent, do not implement exposure hardening. Instead, write the appropriate fix prompt to prompts/claude/ and stop.

## Q&A-specific scope

Implement only the following:

1. Protect public Q&A endpoints with API-key authentication when auth is enabled.

Protect at minimum:

- /chat
- /search
- /search/debug

2. Keep /health accessible without secrets.

The /health endpoint should remain usable for deployment checks.

3. Gate /search/debug behind admin authentication or an explicit debug setting.

Debug output must not be available to unauthenticated public users in production-like settings.

4. Add simple in-process rate limiting if no existing rate-limit mechanism exists.

Use existing configuration patterns.

Do not add Redis, databases, queues, or external services.

5. Add CORS allowlist configuration if CORS is currently missing.

Use explicit allowed origins from config.

Keep local development usable through safe defaults.

6. Keep the Q&A API practical for local development.

Authentication should be controlled by explicit config.

Do not hardcode real API keys.

Do not require auth in tests unless the test explicitly enables it.

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
- no-answer changes
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
- Prefer targeted tests first.

## Targeted tests to add or update

Add targeted tests only for:

- unauthenticated /chat is rejected when auth is enabled
- authenticated /chat is accepted when auth is enabled
- unauthenticated /search is rejected when auth is enabled
- /search/debug is rejected without admin or debug permission
- /health remains accessible without authentication
- rate limit blocks excessive requests when enabled
- CORS allowlist behavior if CORS is implemented

If existing admin auth tests exist, extend them instead of duplicating a separate auth framework.

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
- Prompt001 readiness check
- Prompt002 readiness check
- Prompt003 readiness check
- Prompt004 readiness check
- relevant API/auth files found

2. Implementation summary

Include:

- files changed
- auth behavior added
- rate limit behavior added
- CORS behavior added
- debug endpoint protection added
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

prompts/claude/prompt006_phase1b_qa_citation_integrity.md

The next prompt should cover Q&A citation integrity and fallback metrics only.

Do not execute Prompt006 in this run.
