# Prompt009: Phase 3-A Normalized-Question Answer Cache

You are working in:

/home/rai/chatbot

## Goal

Implement Phase 3-A: an opt-in, bounded, staleness-safe answer cache for /chat so repeated identical questions skip retrieval and generation entirely.

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

1. Config knobs (config.py):

- ANSWER_CACHE_ENABLED (default false — current behavior unchanged)
- ANSWER_CACHE_MAX_ENTRIES (default 256)

2. A small cache module (e.g. rag_core/answer_cache.py): thread-safe LRU keyed on a composite of:

- normalized question (reuse rag_core/question_normalization.normalize_question_for_exact_match)
- corpus state: mtime/size of CHUNKS_JSONL_PATH and the approved-QA file path when enabled, plus top_k and max_context_chars

Any change in corpus state changes the key, so stale answers are never served after re-ingest or approved-QA updates. Provide a clear() hook for tests.

3. Wire into /chat only (webapi/main.py):

- Cache check AFTER the approved exact-match lookup (approved answers stay deterministic and uncached).
- Cache only successful grounded responses (guard_reason is null and used_fallback is false). Never cache fallback, guard, or error responses.
- On hit, return the cached response dict; add no new response fields.
- Audit events should include a cache_hit boolean (audit only, not the response).

4. Do NOT wire into /chat/stream, /search, /search/debug, or product preview.

5. Add targeted tests only for:

- disabled by default: second identical call invokes the pipeline again (call counting)
- enabled: second identical call returns the cached dict without invoking the pipeline
- guard/fallback responses are never cached
- corpus file mtime change invalidates the key
- LRU eviction at max entries
- approved exact-match path is unaffected (still uncached and checked first)

## Explicit non-goals

Do not implement these in this prompt:

- embedding caches or retrieval caches
- caching for streaming or preview endpoints
- distributed/persistent caches (in-process only)
- TTL semantics beyond corpus-state keying
- changing retrieval/guard/citations
- new dependencies
- broad refactors

## Constraints

- No new dependencies.
- Do not read or print .env.
- Do not expose secrets.
- /chat response field set unchanged; a cache hit must be byte-identical to the original response.
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
  --output runs/eval/prompt009_smoke_check.json

If available and safe, run scripts/product_readiness_smoke.sh.

## Required final output

Report in this exact order:

1. Preconditions (repo path, branch, initial git status summary, relevant files found; verify Prompt008 is complete — /chat/stream exists with answer_query_stream — before implementing)
2. Implementation summary (files changed, exact behavior added, explicit non-goals preserved)
3. Verification results (targeted tests, collect-only, eval smoke, smoke script if run, any skipped verification and why)
4. Git diff summary (git diff --stat, no large diffs)
5. Final judgment: PASS / PARTIAL / FAIL, and whether it is safe to continue to Prompt010.
6. Next prompt file: if PASS, write exactly one next recommended prompt to prompts/claude/prompt010_phase3b_observability.md covering operational metrics only (per-stage latency breakdown in the trace — embed/retrieve/rerank/generate; /metrics counters for answer_mode, guard_reason, used_fallback, cache_hit, provider error_type; no new dependencies, in-process counters with a documented multi-worker caveat). Do not execute Prompt010 in this run.

Final clarification before execution:

Cache scope:

- Implement answer cache for non-streaming POST /chat only.
- Do not cache /chat/stream.
- Do not cache /search, /search/debug, product preview, retrieval results, embeddings, or raw provider responses.
- Do not add Redis, disk persistence, database tables, or new dependencies.

Cache enablement:

- ANSWER_CACHE_ENABLED defaults to false.
- With ANSWER_CACHE_ENABLED unset or false, /chat behavior must remain unchanged and the cache must be bypassed completely.

Cache key and staleness:

- Key must include normalized question text.
- Key must include corpus state sufficient to prevent stale answers after corpus or approved-QA updates.
- Include at minimum CHUNKS_JSONL_PATH identity, mtime, size, approved-QA path identity when enabled, approved-QA mtime/size when present, top_k, and max_context_chars.
- If a required corpus or approved-QA file is missing, represent that missing state explicitly in the key.
- If a reliable corpus-state signal cannot be found safely, stop and report PARTIAL rather than implementing a stale cache.

Cache eligibility:

- Cache only clean grounded successful answers.
- Do not cache guard/no-answer responses.
- Do not cache extractive fallback responses.
- Do not cache provider errors or internal errors.
- Do not cache responses with used_fallback=true or non-null guard_reason.
- Approved exact-match behavior remains authoritative and must be checked before cache lookup.

Response contract:

- Do not add cache_hit to the /chat response payload.
- cache_hit may be recorded in audit/logging only.
- A cache hit response must be value-identical to the original cached response dict.

Concurrency and bounds:

- Cache must be bounded LRU.
- Cache access must be thread-safe.
- Add tests for eviction, disabled behavior, hit behavior, fallback non-caching, approved path precedence, and stale-key prevention.

Scope control:

- Do not change retrieval, guard, citations, auth, CORS, streaming, or tenant behavior.
- Do not add new dependencies.
- Tests must not require network access or an OpenAI API key.

If any instruction conflicts, follow this Final clarification section.
