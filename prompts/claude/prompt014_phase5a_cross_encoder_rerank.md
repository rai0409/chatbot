# Prompt014: Phase 5-A Profile-Gated Cross-Encoder Rerank

This prompt adapts the plan in prompts/claude/prompt013_phase5a_cross_encoder_rerank.md. It is renumbered to Prompt014 because the prompt013 slot was used by the security prompt (API key to tenant authorization mapping), which is now complete. Do not delete the original prompt013_phase5a file.

You are working in:

/home/rai/chatbot

## Goal

Implement Phase 5-A: an optional, profile-gated cross-encoder rerank stage for the fused retrieval candidates. Off by default; promoted only through the existing eval gates.

This begins the accuracy track from the original readiness report (the heuristic reranker in rag_core/reranker.py has no semantic stage).

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

## Preconditions to verify before implementing

- Prompt012 is complete: Dockerfile / docker-compose.yml / .env.example / CI workflow present.
- Prompt013 security (API key to tenant authorization) is complete: webapi/api_auth.py exposes ApiAuthContext and enforce_tenant_authorization, and tests/test_api_key_tenant_authorization.py passes.

## Scope

Implement only the following:

1. Config knobs (config.py):

- CROSS_ENCODER_RERANK_ENABLED (default false — everything unchanged by default)
- CROSS_ENCODER_MODEL (default "BAAI/bge-reranker-v2-m3")
- CROSS_ENCODER_TOP_N (default 20 — only the fused top-N are scored)

2. A new module rag_core/cross_encoder_reranker.py:

- Lazy model loading via the same optional-import pattern as rag_core/embedding_provider.py (sentence_transformers CrossEncoder; raise a clear RuntimeError naming the missing dependency and the env knob if unavailable).
- rerank(question, chunks) -> List[RetrievedChunk]: scores (question, searchable_text or text) pairs for the top-N candidates, stores the score in metadata["cross_encoder_score"], reorders those N by score descending, and leaves the tail order unchanged.
- Must never crash the pipeline: on model failure, log one structured WARNING per process and return the input order unchanged.

3. Wire into rag_core/qa.py _retrieve_and_rerank AFTER the existing heuristic rerank and keyword boost, BEFORE parent expansion, gated on the config knob. The existing heuristic reranker stays as-is. Tenant filtering must be unaffected: the cross-encoder reorders already-tenant-filtered candidates only.

4. Eval integration: add a "hybrid_rerank_ce" retrieval mode to eval/runner.py (same bridge pattern as the existing modes) so the cross-encoder can be compared with eval/rerank_promotion_gate.py before any default change.

5. Add targeted tests only for (fake CrossEncoder, no model download, no network):

- disabled by default: rerank stage not invoked
- enabled: top-N reordered by fake scores, cross_encoder_score in metadata, tail untouched
- model-load failure: warns once and returns input order
- missing sentence-transformers: clear RuntimeError message from the loader (only when explicitly invoked)
- qa pipeline wiring: cross-encoder runs after heuristic rerank and before parent expansion (call-order assertion with monkeypatched stages)

## Explicit non-goals

Do not implement these in this prompt:

- adding sentence-transformers to requirements.txt (it stays optional, as for local embeddings)
- changing the heuristic reranker or guard
- LLM-based reranking
- changing default behavior (the knob defaults to false)
- model download in CI or tests
- changes to API auth or tenant authorization
- broad refactors

## Constraints

- No new required dependencies.
- Do not read or print .env.
- Do not expose secrets.
- Default behavior byte-identical: deterministic eval smoke must remain 21/21 without the knob.
- Tests must not require network access, model downloads, or an OpenAI API key.
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
  --output runs/eval/prompt014_smoke_check.json

If available and safe, run scripts/product_readiness_smoke.sh.

Also run the tenant authorization tests to confirm the security layer is untouched:

python -m pytest tests/test_api_key_tenant_authorization.py tests/test_api_auth.py tests/test_tenant_isolation.py -q

Document (do not run) the promotion path: a retrieval-aware eval comparing hybrid_rerank vs hybrid_rerank_ce with --real-vector on a stamped collection, gated by eval/rerank_promotion_gate.py.

## Required final output

Report in this exact order:

1. Preconditions (repo path, branch, initial git status summary, relevant files found; verify Prompt012 packaging and Prompt013 security are complete before implementing)
2. Implementation summary (files changed, exact behavior added, explicit non-goals preserved)
3. Verification results (targeted tests, collect-only, eval smoke, smoke script if run, any skipped verification and why)
4. Git diff summary (git diff --stat, no large diffs)
5. Final judgment: PASS / PARTIAL / FAIL, and whether it is safe to continue to Prompt015.
6. Next prompt file: if PASS, write exactly one next recommended prompt to prompts/claude/prompt015_phase5b_qa_pair_chunks.md covering approved Q&A → Q+A pair RAG chunks (the README's documented-but-unbuilt route 3: convert approved records into canonical Q+A pair chunks so retrieval sees question and answer together, with an ingest script and eval cases). Do not execute Prompt015 in this run.
