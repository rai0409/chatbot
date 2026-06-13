# Prompt003: Phase 0-C Q&A Retrieval Latency Optimization

You are working in:

/home/rai/chatbot

## Goal

Implement Phase 0-C for Q&A chatbot usage: reduce avoidable retrieval latency in the RAG question-answering path while preserving answer quality and all existing /chat and /search response contracts.

This prompt is specifically for Q&A performance, not broad performance refactoring.

## Execution mode

Proceed autonomously.

Do not ask for human confirmation for ordinary local edits, targeted tests, smoke checks, or local verification.

Stop only if one of the following occurs:

- A destructive operation would be required.
- User data would be deleted.
- .env, secrets, tokens, API keys, or private credentials would need to be read, printed, changed, or inferred.
- A remote push, force push, or remote deployment would be required.
- The active Q&A retrieval flow cannot be identified safely.
- The target files cannot be found and the correct location is ambiguous.
- Verification fails in a way that cannot be safely classified after one bounded fix attempt.

If verification fails because of your changes, perform one bounded fix attempt, rerun targeted verification, and report final status.

## Preconditions

Before implementing, check whether Prompt001 and Prompt002 appear complete.

Required Prompt001 signs:

- /health exposes keyword_index_loaded
- /health exposes keyword_index_records
- /health exposes keyword_index_path
- missing or empty keyword index state is visible

Required Prompt002 signs:

- embedding provider/model consistency is checked or clearly surfaced
- provider/model mismatch cannot silently pass in the query path

If these signs are absent, do not implement Phase 0-C. Instead, write the appropriate fix prompt to prompts/claude/ and stop.

## Q&A-specific scope

Implement only the following:

1. Identify the main Q&A retrieval path used by /chat.

Likely target areas include:

- rag_core/qa.py
- rag_core/retrieval.py
- rag_core/store.py
- existing embedding provider module
- existing tests related to QA retrieval

2. Avoid duplicate retrieval when the augmented query is identical to the original question.

If augmented_query equals the base question after normalization, run only one retrieval pass.

3. Batch base and augmented query embeddings into one embedding call when both queries are genuinely needed.

The goal is to avoid two sequential embedding round-trips for one Q&A request.

4. Reuse a module-level chromadb.PersistentClient singleton in the existing store layer.

The goal is to avoid constructing a new PersistentClient for every Q&A retrieval call.

5. Add minimal stage timing visibility if an existing trace or timing object already exists.

Prefer existing trace fields. Do not introduce a new metrics framework.

If safe and localized, add timings for:

- embedding
- retrieval
- reranking
- total retrieval path

6. Preserve Q&A answer behavior.

Do not intentionally change:

- approved exact-match behavior
- citation format
- answer text format
- no-answer behavior
- ranking semantics, except where duplicate work is removed without changing results

## Explicit non-goals

Do not implement these in this prompt:

- streaming
- SSE
- caching
- LLM timeout or retry
- real confidence guard calibration
- no-answer behavior changes
- citation validation changes
- auth/rate limiting
- CORS changes
- tenant isolation changes
- Docker/CI changes
- cross-encoder reranker
- LLM query rewriting
- broad refactors
- unrelated formatting changes

## Constraints

- No new dependencies.
- Do not read or print .env.
- Do not expose secrets.
- Do not change /chat or /search API response contracts.
- Do not change existing approved QA exact-match outputs.
- Do not change external API behavior except internal latency improvement and optional trace/timing visibility.
- Keep changes minimal and localized.
- Do not run full test suites unless targeted verification clearly requires it.
- Prefer targeted tests first.

## Targeted tests to add or update

Add targeted tests only for:

- identical augmented query skips the second retrieval pass
- base and augmented query embeddings are batched when both are needed
- Chroma PersistentClient construction is not repeated per retrieval call
- Q&A retrieval output remains structurally compatible
- approved exact-match Q&A path remains unaffected

If existing tests already cover part of this, update the minimal relevant tests instead of adding redundant ones.

## Verification

Run targeted tests first.

Then run:

python -m pytest --collect-only

If available and safe, run:

scripts/product_readiness_smoke.sh

If there is a local retrieval smoke or eval that does not require external network access, run the smallest Q&A retrieval smoke only.

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
- relevant Q&A retrieval files found

2. Implementation summary

Include:

- files changed
- exact latency behavior added
- whether duplicate retrieval was skipped
- whether embedding calls were batched
- whether Chroma client reuse was implemented
- explicit non-goals preserved

3. Verification results

Include:

- targeted tests
- pytest collect-only
- product readiness smoke, if run
- local Q&A retrieval smoke, if run
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

If final judgment is PASS, write exactly one next recommended prompt to:

prompts/claude/prompt004_phase1a_qa_confidence_guard.md

If prompts/claude/backlog/prompt004_phase1a_qa_confidence_guard.md exists, use it as the source.

Do not execute Prompt004 in this run.
