# Prompt003: Phase 0-C Retrieval Batching And Chroma Client Singleton

You are working in:

/home/rai/chatbot

## Goal

Implement Phase 0-C: query-embedding batching and a Chroma client singleton only.

Reduce per-request retrieval latency without changing retrieval results, response contracts, or ranking behavior.

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

1. Skip the redundant second retrieval pass: in rag_core/qa.py _retrieve_and_rerank, when the augmented query equals the base query, run hybrid retrieval once and reuse the result instead of running two identical passes.

2. Batch query embeddings: when the base and augmented queries differ, embed both in a single embed_queries call instead of two sequential calls. Add a way for hybrid/vector retrieval to accept a precomputed query embedding (for example an optional query_embedding parameter threaded through hybrid_retrieve and vector_retrieve) so the second pass does not re-embed. Keep the existing single-query call path working unchanged for all other callers.

3. Chroma client singleton: in rag_core/store.py, reuse a module-level chromadb.PersistentClient keyed by the resolved vectorstore path instead of constructing a new client on every get_vectorstore call. Keep get_or_create_collection per call. Provide a small reset hook for tests.

4. Add targeted tests only for:

- identical base/augmented query results in exactly one hybrid retrieval pass (count calls with a fake)
- differing queries embed both texts in one embed_queries call (assert call count and batch size with a fake)
- precomputed query_embedding is used (no embed call) when provided to vector_retrieve
- store.get_vectorstore reuses the same underlying client across calls and the reset hook clears it

## Explicit non-goals

Do not implement these in this prompt:

- changing fusion/rerank/guard logic or any ranking behavior
- caching of answers or embeddings across requests
- async/streaming changes
- embedding provider/model changes beyond what Prompt002 already did
- auth/rate limiting/CORS
- tenant isolation
- Docker/CI changes
- broad refactors

## Constraints

- No new dependencies.
- Do not read or print .env.
- Do not expose secrets.
- Do not change /chat, /search, /search/debug response contracts.
- Retrieval results for the same inputs must be unchanged (same chunks, same order).
- Tests must not require network access or an OpenAI API key.
- Keep changes minimal and localized.
- Do not run full test suites unless targeted verification clearly requires it.

## Verification

Run targeted tests first.

Then run:

python -m pytest --collect-only

If available and safe, run:

scripts/product_readiness_smoke.sh

Also run the deterministic eval smoke to confirm unchanged retrieval behavior:

PYTHONPATH=. .venv/bin/python -m eval.runner \
  --cases eval/cases/smoke_cases.jsonl \
  --chunks-jsonl eval/cases/smoke_chunks.jsonl \
  --output runs/eval/smoke_results.json

## Required final output

Report in this exact order:

1. Preconditions (repo path, branch, initial git status summary, relevant files found)
2. Implementation summary (files changed, exact behavior added, explicit non-goals preserved)
3. Verification results (targeted tests, pytest collect-only, smoke if run, eval smoke comparison, any skipped verification and why)
4. Git diff summary (git diff --stat, no large diffs)
5. Final judgment: PASS / PARTIAL / FAIL, and whether it is safe to continue to Prompt004.
6. Next prompt file: if PASS, write exactly one next recommended prompt to prompts/claude/prompt004_phase1a_confidence_guard.md covering the real similarity guard (guard on raw vector distance / BM25 evidence instead of rank-derived pseudo-distances, calibrated against the existing retrieval-aware eval with expected_abstain labels) only. Do not execute Prompt004 in this run.
