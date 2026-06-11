# Prompt008: Phase 2-C Streaming Chat Endpoint

You are working in:

/home/rai/chatbot

## Goal

Implement Phase 2-C: an opt-in SSE streaming variant of /chat. The existing non-streaming /chat must remain byte-identical in behavior.

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

1. New endpoint POST /chat/stream (same ChatRequest body, same api auth dependency as /chat) returning text/event-stream via StreamingResponse. Event protocol (SSE `data:` lines, JSON payloads, one `event:` name each):

- event: approved — full approved exact-match payload (the approved path does not stream; emit one event and end)
- event: meta — request_id, intent, query_type, guard_reason, used_fallback (emitted after retrieval/guard, before generation)
- event: delta — {"text": "..."} chunks as generation tokens arrive
- event: final — the complete AnswerResult.to_dict() payload (citations validated/built exactly as the non-streaming path, including extractive fallback if validation fails — the final event is authoritative and may correct the streamed text)
- event: error — {"detail": ..., "error_type": ...} reusing _generation_error_payload classification

2. Streaming generation: extend the qa layer with a streaming variant that uses client.chat.completions.create(..., stream=True) with the same timeout/max_tokens/retry policy from Prompt007 (retry only before the first token; never mid-stream). Guard/no-answer paths emit meta then final immediately (no deltas).

3. Reuse, do not duplicate: retrieval/guard/citation logic must come from the existing _build_retrieval_trace / _build_answer_result helpers. No copy-pasted pipeline.

4. Audit: append one chat audit event per stream (same fields as /chat), after the final/error event is determined.

5. Add targeted tests only for (fake streaming clients, no network):

- guard-fired stream: meta then final, no deltas, citations []
- grounded stream: deltas concatenate to the raw text; final payload equals the non-streaming result for the same fake outputs
- validation-failure stream: final carries extractive fallback with real citations and used_fallback true
- approved exact-match: single approved event
- provider error before first token: single error event with error_type
- /chat/stream route carries the api auth dependency; /chat behavior unchanged

## Explicit non-goals

Do not implement these in this prompt:

- changing /chat
- async conversion of other endpoints
- websockets
- caching
- changing retrieval/guard/citations
- new dependencies
- broad refactors

## Constraints

- No new dependencies (FastAPI StreamingResponse only).
- Do not read or print .env.
- Do not expose secrets.
- Tests must not require network access or an OpenAI API key.
- Keep changes minimal and localized.
- Do not run full test suites unless targeted verification clearly requires it.

## Verification

Run targeted tests first.

Then run:

python -m pytest --collect-only

Run the deterministic eval smoke (must remain 21/21):

PYTHONPATH=. .venv/bin/python -m eval.runner \
  --cases eval/cases/smoke_cases.jsonl \
  --chunks-jsonl eval/cases/smoke_chunks.jsonl \
  --output runs/eval/prompt008_smoke_check.json

If available and safe, run scripts/product_readiness_smoke.sh.

## Required final output

Report in this exact order:

1. Preconditions (repo path, branch, initial git status summary, relevant files found; verify Prompt007 is complete — _create_chat_completion with retry/timeout exists — before implementing)
2. Implementation summary (files changed, exact behavior added, explicit non-goals preserved)
3. Verification results (targeted tests, collect-only, eval smoke, smoke script if run, any skipped verification and why)
4. Git diff summary (git diff --stat, no large diffs)
5. Final judgment: PASS / PARTIAL / FAIL, and whether it is safe to continue to Prompt009.
6. Next prompt file: if PASS, write exactly one next recommended prompt to prompts/claude/prompt009_phase3a_answer_cache.md covering a normalized-question answer cache for /chat (LRU keyed on normalized question + corpus state, bounded size, opt-in via env, bypass on cache-miss-only semantics, no staleness across approved-QA or corpus updates). Do not execute Prompt009 in this run.
