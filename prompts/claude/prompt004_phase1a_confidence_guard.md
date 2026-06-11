# Prompt004: Phase 1-A Real Confidence Guard

You are working in:

/home/rai/chatbot

## Goal

Implement Phase 1-A: make the no-answer guard use real evidence signals instead of rank-derived pseudo-distances.

Today, hybrid/keyword hits carry fabricated scores (0.25 + 0.02 * (rank - 1), see rag_core/retrieval.py), so the distance thresholds in guard_merged_top (rag_core/qa.py, RAG_HARD_MAX_DIST / RAG_SOFT_DIST_*) effectively never fire on the hybrid path. Abstention currently rests almost entirely on salient_mismatch / too_general heuristics.

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

1. Preserve real evidence signals on retrieved chunks: ensure each RetrievedChunk's metadata keeps the raw vector distance (when a vector hit) and raw BM25 score (when a keyword hit) through fusion — most of this already exists (metadata["bm25_score"], vector distance as the vector hit score, metadata["rrf_score"]); expose the raw vector distance explicitly in metadata (for example metadata["vector_distance"]) so fusion does not erase it.

2. Add a guard evidence summary: a small helper that, given the post-rerank candidates, returns the best raw vector distance (None if no vector hit), the best BM25 score (None if no keyword hit), and whether any candidate had a real lexical match (reuse existing score_details / keyword_score where possible).

3. Rework guard_merged_top distance checks to use the evidence summary:

- hard_distance / soft_distance fire on the best RAW vector distance, not pseudo-distances.
- When there is no vector evidence at all (vector stubbed or empty), distance checks must not fire spuriously; keyword-only evidence falls back to a minimum BM25/keyword-evidence requirement instead.
- Keep salient_mismatch and too_general behavior unchanged.
- Keep DISABLE_GUARD behavior unchanged.

4. Keep all existing config knobs working (RAG_HARD_MAX_DIST, RAG_SOFT_DIST_*, deltas). Add at most one new knob if needed for the keyword-only minimum evidence threshold, with a conservative default that preserves current pass behavior on the smoke corpus.

5. Calibrate and verify against the existing eval harness:

- Deterministic smoke (eval/cases/smoke_cases.jsonl + smoke_chunks.jsonl) must still pass 21/21.
- Retrieval-aware eval (eval/cases/retrieval_cases.jsonl) must not regress abstain_passes or gold hit metrics for bm25_only / hybrid / hybrid_rerank modes; record before/after summaries.

6. Add targeted tests only for:

- guard fires hard_distance on a high raw vector distance even when fused rank-score is low
- guard does not fire distance checks when vector evidence is absent (keyword-only path)
- keyword-only weak evidence triggers the fallback guard reason
- existing guard reasons (salient_mismatch, too_general, no_results) unchanged on representative inputs

## Explicit non-goals

Do not implement these in this prompt:

- changing fusion or rerank ordering
- changing answer generation, citations, or fallback text
- new no-answer response contract fields
- streaming, caching, auth, tenants, Docker/CI
- broad refactors

## Constraints

- No new dependencies.
- Do not read or print .env.
- Do not expose secrets.
- Do not change /chat, /search, /search/debug response contracts (guard_reason values may differ per query as intended by this change; the field set must not change).
- Tests must not require network access or an OpenAI API key.
- Keep changes minimal and localized.
- Do not run full test suites unless targeted verification clearly requires it.

## Verification

Run targeted tests first.

Then run:

python -m pytest --collect-only

Run the deterministic eval smoke and the retrieval-aware eval (deterministic mode) and compare before/after:

PYTHONPATH=. .venv/bin/python -m eval.runner \
  --cases eval/cases/smoke_cases.jsonl \
  --chunks-jsonl eval/cases/smoke_chunks.jsonl \
  --output runs/eval/smoke_results.json

PYTHONPATH=. .venv/bin/python -m eval.runner \
  --retrieval-aware \
  --cases eval/cases/retrieval_cases.jsonl \
  --chunks-jsonl eval/cases/smoke_chunks.jsonl \
  --modes bm25_only,hybrid,hybrid_rerank \
  --per-query-output runs/eval/retrieval_rows_guard.jsonl \
  --summary-output runs/eval/retrieval_summary_guard.json \
  --eval-k 5

If available and safe, run scripts/product_readiness_smoke.sh.

## Required final output

Report in this exact order:

1. Preconditions (repo path, branch, initial git status summary, relevant files found)
2. Implementation summary (files changed, exact behavior added, explicit non-goals preserved)
3. Verification results (targeted tests, collect-only, eval smoke 21/21, retrieval-aware before/after comparison, smoke script if run, any skipped verification and why)
4. Git diff summary (git diff --stat, no large diffs)
5. Final judgment: PASS / PARTIAL / FAIL, and whether it is safe to continue to Prompt005.
6. Next prompt file: if PASS, write exactly one next recommended prompt to prompts/claude/prompt005_phase1b_no_answer_citations.md covering honest no-answer citation behavior (no fabricated [S1] placeholder citations on guard fallback; empty citations with an explicit no-answer mode while keeping the existing response field set) only. Do not execute Prompt005 in this run.
