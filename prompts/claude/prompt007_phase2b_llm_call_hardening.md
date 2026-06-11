# Prompt007: Phase 2-B LLM Call Hardening

You are working in:

/home/rai/chatbot

## Goal

Implement Phase 2-B: harden the chat-completion call only — timeout, bounded retries, output token bound, and structured provider-error responses on /chat.

Today the generation call in rag_core/qa.py (_answer_query_impl, client.chat.completions.create) sets only temperature=0: no timeout, no max_tokens, no retry. A hung provider call blocks a worker indefinitely, and /chat maps every provider failure to a generic 500, while /search/debug already classifies provider errors via _generation_error_payload (webapi/main.py).

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

1. Config knobs (config.py, read like existing knobs, conservative defaults):

- CHAT_COMPLETION_TIMEOUT_SECONDS (default 30)
- CHAT_COMPLETION_MAX_RETRIES (default 1; retries only on timeout / connection errors / 429 / 5xx-style provider errors, never on 4xx auth or validation errors)
- CHAT_COMPLETION_RETRY_BACKOFF_SECONDS (default 1.0, doubled per attempt)
- CHAT_COMPLETION_MAX_TOKENS (default 1024)

2. A small wrapper around the generation call in rag_core/qa.py (e.g. _create_chat_completion(client, messages)) that applies timeout, max_tokens, and the bounded retry policy. Use the OpenAI SDK's per-request options if available (client.chat.completions.create(..., timeout=...)); do not add a dependency.

3. /chat provider-error contract: reuse the existing _generation_error_payload classification in the /chat exception handler so quota/rate-limit/provider-down surface as 429/503 with error_type, instead of generic 500. Keep the generic 500 for everything else. Keep audit logging of the error event.

4. Add targeted tests only for (all with fake clients, no network):

- max_tokens and timeout are passed to the create call
- retry happens on a retryable error and succeeds on the second attempt
- no retry on a non-retryable error (e.g. 401-style)
- retries are bounded (gives up after configured attempts and raises)
- /chat returns 429 with error_type for a rate-limit-style exception and 503 for a provider-5xx-style exception
- /chat still returns 500 for unrelated internal errors

## Explicit non-goals

Do not implement these in this prompt:

- streaming
- answer/embedding caching
- changing prompts, guard, citations, retrieval
- async conversion of endpoints
- rate limiting of inbound requests
- new dependencies
- broad refactors

## Constraints

- No new dependencies.
- Do not read or print .env.
- Do not expose secrets.
- /chat response field set unchanged for success; error responses use the same shape /search/debug already uses ({detail, error_type}).
- Tests must not require network access or an OpenAI API key.
- Use monotonic/sleep injection or monkeypatched sleep in tests; do not make tests slow (no real backoff sleeps).
- Keep changes minimal and localized.
- Do not run full test suites unless targeted verification clearly requires it.

## Verification

Run targeted tests first.

Then run:

python -m pytest --collect-only

If available and safe, run scripts/product_readiness_smoke.sh.

## Required final output

Report in this exact order:

1. Preconditions (repo path, branch, initial git status summary, relevant files found; verify Prompt006 is complete — api_auth wiring present — before implementing)
2. Implementation summary (files changed, exact behavior added, explicit non-goals preserved)
3. Verification results (targeted tests, collect-only, smoke if run, any skipped verification and why)
4. Git diff summary (git diff --stat, no large diffs)
5. Final judgment: PASS / PARTIAL / FAIL, and whether it is safe to continue to Prompt008.
6. Next prompt file: if PASS, write exactly one next recommended prompt to prompts/claude/prompt008_phase2c_streaming_chat.md covering SSE streaming for /chat as a new opt-in endpoint or flag (citation validation after stream completion with a corrective final event; existing non-streaming /chat unchanged). Do not execute Prompt008 in this run.
