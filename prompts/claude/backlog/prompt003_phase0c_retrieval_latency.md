# Prompt003: Phase 0-C Retrieval Latency Optimization

You are working in:

/home/rai/chatbot

## Goal

Implement Phase 0-C: retrieval latency optimization only.

Reduce avoidable retrieval latency while preserving existing /chat and /search response contracts.

Focus on removing redundant embedding calls and avoiding repeated Chroma client construction.

## Execution mode

Proceed autonomously.

Do not ask for human confirmation for ordinary local edits, targeted tests, smoke checks, or local verification.

Stop only if one of the following occurs:

- A destructive operation would be required.
- User data would be deleted.
- .env, secrets, tokens, API keys, or private credentials would need to be read, printed, changed, or inferred.
- A remote push, force push, or remote deployment would be required.
- The active retrieval flow cannot be identified safely.
- The target files cannot be found and the correct location is ambiguous.
- Verification fails in a way that cannot be safely classified after one bounded fix attempt.

If verification fails because of your changes, perform one bounded fix attempt, rerun targeted verification, and report final status.

## Preconditions

Before implementing, check whether Prompt001 and Prompt002 appear complete.

Required Prompt001 signs:

- /health exposes keyword_index_loaded, keyword_index_records, and keyword_index_path

Required Prompt002 signs:

- embedding provider/model metadata or verification path exists
- provider/model mismatch cannot silently pass in the query path

If these signs are absent, do not implement Phase 0-C. Instead, write the appropriate fix prompt to prompts/claude/ and stop.

## Scope

Implement only the following:

1. In the retrieval path used by rag_core/qa.py, avoid running the augmented-query retrieval pass when the augmented query is identical to the base query.

2. Batch base and augmented query embeddings into a single embedding call when both queries are needed.

3. Reuse a module-level chromadb.PersistentClient singleton in rag_core/store.py or the existing store layer.

4. Preserve retrieval ordering and response contract as much as possible.

5. Add targeted tests only for:

- augmented query identical to base query skips second retrieval pass
- base and augmented query embeddings are batched when both are needed
- Chroma PersistentClient construction is not repeated for every retrieval call
- existing retrieval outputs remain structurally compatible

## Explicit non-goals

Do not implement these in this prompt:

- embedding provider/model metadata changes
- BM25 corpus script changes
- real similarity guard calibration
- no-answer behavior changes
- citation validation changes
- streaming
- auth/rate limiting
- CORS changes
- tenant isolation changes
- Docker/CI changes
- broad refactors
- unrelated formatting changes

## Constraints

- No new dependencies.
- Do not read or print .env.
- Do not expose secrets.
- Do not change /chat or /search API response contracts.
- Do not alter scoring semantics except where required to remove duplicate retrieval work.
- Keep changes minimal and localized.
- Do not run full test suites unless targeted verification clearly requires it.
- Prefer targeted tests first.

## Verification

Run targeted tests first.

Then run:

python -m pytest --collect-only

If available and safe, run:

scripts/product_readiness_smoke.sh

If there is an existing eval runner that does not require external network access, run the smallest retrieval smoke only.

Do not run broad, slow, or external-network-dependent tests unless necessary.

## Required final output

Report in this exact order:

1. Preconditions

Include:

- repo path
- branch
- initial git status summary
- Prompt001 and Prompt002 readiness check
- relevant files found

2. Implementation summary

Include:

- files changed
- exact latency behavior added
- what was intentionally not changed

3. Verification results

Include:

- targeted tests
- pytest collect-only
- product readiness smoke, if run
- smallest retrieval smoke, if run
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

Also state whether this is safe to continue to Prompt004.

6. Next prompt file

If final judgment is PASS, promote or write exactly one next recommended prompt to:

prompts/claude/prompt004_phase1a_real_confidence_guard.md

If prompts/claude/backlog/prompt004_phase1a_real_confidence_guard.md exists, use it as the source.

Do not execute Prompt004 in this run.
