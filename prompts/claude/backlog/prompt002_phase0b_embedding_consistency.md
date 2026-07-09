# Prompt002: Phase 0-B Embedding Consistency

You are working in:

/home/rai/chatbot

## Goal

Implement Phase 0-B: embedding provider/model consistency and Chroma collection metadata fingerprinting only.

Prevent silent mismatch between the embedding provider/model used at ingest time and the embedding provider/model used at query time.

## Execution mode

Proceed autonomously.

Do not ask for human confirmation for ordinary local edits, targeted tests, smoke checks, or local verification.

Stop only if one of the following occurs:

- A destructive operation would be required.
- User data would be deleted.
- .env, secrets, tokens, API keys, or private credentials would need to be read, printed, changed, or inferred.
- A remote push, force push, or remote deployment would be required.
- Chroma collection metadata cannot be safely inspected or updated without data migration ambiguity.
- The target files cannot be found and the correct location is ambiguous.
- Verification fails in a way that cannot be safely classified after one bounded fix attempt.

If verification fails because of your changes, perform one bounded fix attempt, rerun targeted verification, and report final status.

## Preconditions

Before implementing, check whether Prompt001 appears complete.

Required Prompt001 signs:

- /health exposes keyword_index_loaded, keyword_index_records, and keyword_index_path
- retrieval corpus missing or empty state is visible through warning or status
- pytest collect-only still succeeds

If these signs are absent, do not implement Phase 0-B. Instead, write a fix prompt to:

prompts/claude/prompt001_fix_retrieval_corpus_integrity.md

Then stop.

## Scope

Implement only the following:

1. Identify the active embedding provider and embedding model from existing config/helper functions.

2. Ensure there is a single source of truth for default embedding provider selection.

3. In the ingest path, stamp Chroma collection metadata with:

- embed_provider
- embed_model

If an existing dimension or model name is already available safely, also include:

- embed_dimension

Only include embed_dimension if it is already available without extra provider calls.

4. In the query path, verify that the active embedding provider/model matches the collection metadata.

5. If there is a mismatch, raise a clear RuntimeError with a safe message.

The error message must include:

- active provider
- active model
- collection provider
- collection model
- collection name or path if safely available

The error message must not include secrets or .env values.

6. Update webapi/main.py embedding client creation so it uses the same provider default source as the core embedding provider module.

7. Add targeted tests only for:

- metadata stamp at ingest
- matching provider/model query path succeeds
- mismatched provider/model query path raises clear RuntimeError
- webapi/main.py no longer has an independent hardcoded default provider inconsistent with the core module

## Explicit non-goals

Do not implement these in this prompt:

- retrieval batching
- Chroma client singleton
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
- Do not re-embed the production corpus.
- Do not delete or recreate vectorstore data.
- Keep behavior backward-compatible where no metadata exists, unless mismatch can be safely proven.
- If existing collections lack metadata, return a clear warning or compatibility path rather than destructive migration.
- Keep changes minimal and localized.
- Do not run full test suites unless targeted verification clearly requires it.

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
- relevant files found

2. Implementation summary

Include:

- files changed
- exact behavior added
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

Also state whether this is safe to continue to Prompt003.

6. Next prompt file

If final judgment is PASS, promote or write exactly one next recommended prompt to:

prompts/claude/prompt003_phase0c_retrieval_latency.md

If prompts/claude/backlog/prompt003_phase0c_retrieval_latency.md exists, use it as the source.

Do not execute Prompt003 in this run.
