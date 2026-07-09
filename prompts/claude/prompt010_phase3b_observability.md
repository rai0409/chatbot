# Prompt010: Phase 3-B Operational Metrics

You are working in:

/home/rai/chatbot

## Goal

Implement Phase 3-B: operational observability only — per-stage latency in the trace and aggregate counters on /metrics.

Today /metrics returns three in-process numbers (uptime, total_requests, error_requests), and the trace carries only a single end-to-end latency_ms. There is no visibility into where time goes (retrieval vs generation) or how often guard/fallback/cache paths fire.

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

1. Per-stage latency in the qa trace (rag_core/qa.py): add a stage_latency_ms dict to the trace with keys retrieval_ms (covering _retrieve_and_rerank inside _build_retrieval_trace) and generation_ms (the LLM call, when it runs; 0 or absent for guard/approved paths). Use time.perf_counter() deltas. Do not change existing trace keys; only add.

2. A small in-process metrics module (e.g. webapi/metrics_registry.py): thread-safe counters keyed by (name, label-value), with increment(name, label=None) and snapshot() helpers, plus a reset() hook for tests.

3. Wire counters in webapi/main.py for /chat, /chat/stream, /search/debug:

- chat_answer_mode_total labeled by answer_mode (approved_exact_match / grounded / fallback)
- chat_guard_reason_total labeled by guard_reason (only when non-null)
- chat_used_fallback_total (no label)
- chat_cache_hit_total (no label; /chat cache hits from Prompt009)
- chat_provider_error_total labeled by error_type (rate_limited / provider_unavailable / insufficient_quota)

4. Extend GET /metrics: keep the existing three fields unchanged; add a "counters" object from snapshot(). Document the multi-worker caveat (counters are per-process) in a one-line comment and in docs/production_readiness_checklist.md.

5. Add targeted tests only for:

- stage_latency_ms present in trace with retrieval_ms >= 0 for guard and grounded paths; generation_ms present only for grounded
- counters increment correctly for grounded, guard-fallback, approved, cache-hit, and provider-error chat calls (fake pipeline/clients, reuse existing test patterns)
- /metrics response includes the existing fields plus counters
- reset() hook clears counters

## Explicit non-goals

Do not implement these in this prompt:

- Prometheus client or any new dependency
- persistent or cross-process metrics
- tracing systems (OpenTelemetry)
- log format changes
- changing retrieval/guard/citations/caching behavior
- broad refactors

## Constraints

- No new dependencies.
- Do not read or print .env.
- Do not expose secrets (no question text or tokens in metrics labels — only enum-like values).
- /chat, /chat/stream, /search/debug response contracts unchanged; /metrics only gains the counters object.
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
  --output runs/eval/prompt010_smoke_check.json

If available and safe, run scripts/product_readiness_smoke.sh.

## Required final output

Report in this exact order:

1. Preconditions (repo path, branch, initial git status summary, relevant files found; verify Prompt009 is complete — answer_cache wired into /chat — before implementing)
2. Implementation summary (files changed, exact behavior added, explicit non-goals preserved)
3. Verification results (targeted tests, collect-only, eval smoke, smoke script if run, any skipped verification and why)
4. Git diff summary (git diff --stat, no large diffs)
5. Final judgment: PASS / PARTIAL / FAIL, and whether it is safe to continue to Prompt011.
6. Next prompt file: if PASS, write exactly one next recommended prompt to prompts/claude/prompt011_phase4a_tenant_isolation.md covering tenant isolation in the retrieval layer (tenant_id in chunk metadata at ingest, tenant filter in the Chroma where clause and BM25 metadata filter, tenant threading through /chat; default tenant preserves current single-tenant behavior). Do not execute Prompt011 in this run.

Final clarification before execution:

Metrics scope:

- Implement operational observability only.
- Do not add Prometheus client, OpenTelemetry, Redis, database tables, external exporters, or new dependencies.
- Metrics must be in-process only and thread-safe.
- Document that counters are per-process and not globally aggregated across multiple workers.

Response contract:

- Do not add metrics fields to /chat, /chat/stream, /search, or product-preview normal response payloads.
- Existing response field sets must remain unchanged.
- /metrics may add only the counters object while preserving the existing uptime, total_requests, and error_requests fields.
- stage_latency_ms may be added only to existing trace/debug structures where trace data is already expected.

Latency tracking:

- Add per-stage latency only where the code already has clear stage boundaries.
- Prefer coarse safe stages over invasive refactors:
  - retrieval_ms for _retrieve_and_rerank / retrieval-trace work
  - generation_ms for LLM generation only when it runs
- Do not rewrite retrieval or generation pipelines just to get perfect timing.
- For guard/approved paths, generation_ms should be 0 or absent, consistently tested.

Counters:

- Count answer_mode, guard_reason, used_fallback, cache_hit, and provider error_type.
- Use stable enum-like labels only.
- Avoid unbounded labels such as raw user queries, file paths, exception messages, request IDs, or tokens.
- Never record secrets or request bodies in metrics.

Scope control:

- Do not change retrieval, guard, citations, auth, CORS, streaming, cache semantics, or tenant behavior.
- Do not add new dependencies.
- Tests must not require network access or an OpenAI API key.

If any instruction conflicts, follow this Final clarification section.
