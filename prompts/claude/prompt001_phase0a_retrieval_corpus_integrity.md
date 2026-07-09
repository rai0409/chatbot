# Prompt001: Phase 0-A Retrieval Corpus Integrity

You are working in:

/home/rai/chatbot

## Goal

Implement Phase 0-A: retrieval corpus integrity only.

Make the RAG retrieval layer visibly report when the configured keyword/BM25 corpus is missing or empty, and expose that state through the existing /health endpoint.

This prompt must not change normal /chat or /search response contracts.

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

1. In rag_core/retrieval.py, detect whether the configured keyword corpus file is missing or present but loads zero usable records.

2. Log a structured WARNING once per process when the keyword corpus is missing or empty.

The warning must include these fields:

- keyword_index_loaded
- keyword_index_records
- keyword_index_path
- reason

3. Expose the following fields in the existing /health endpoint payload:

- keyword_index_loaded
- keyword_index_records
- keyword_index_path

4. Add a minimal local script:

scripts/build_canonical_index.py

The script should concatenate and deduplicate index/*.jsonl into:

index/chunks.canonical.bytype.dedup.jsonl

Reuse existing dedup logic if available, especially any logic in tools/dedup_chunks_by_id.py.

Do not invent a new complex indexing pipeline.

5. Add targeted tests only for:

- missing corpus state/warning
- empty corpus state/warning
- /health includes the new keyword index fields
- scripts/build_canonical_index.py creates the expected output from small fixture JSONL files

## Explicit non-goals

Do not implement these in this prompt:

- embedding provider/model consistency
- Chroma collection metadata fingerprinting
- retrieval batching
- Chroma client singleton
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
- Do not change existing /chat or /search API contracts.
- Only add fields to /health.
- Keep changes minimal and localized.
- Do not run full test suites unless targeted verification clearly requires it.
- Prefer targeted tests first.
- Preserve existing behavior except for visibility of missing/empty keyword corpus state.

## Expected implementation details

Prefer a small status helper in the retrieval layer.

The helper should return a dictionary-like status with these keys:

- keyword_index_loaded: boolean
- keyword_index_records: integer
- keyword_index_path: string

Use existing config values instead of hardcoded paths when possible.

The warning should not spam logs repeatedly on every request. Emit it once per process for the same missing/empty state.

If the current code silently returns an empty keyword index when the file is missing, preserve the non-crashing runtime behavior for now, but make the degraded state visible through logs and /health.

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

Also state whether this is safe to continue to Prompt002.

6. Next prompt file

If final judgment is PASS, write exactly one next recommended prompt to:

prompts/claude/prompt002_phase0b_embedding_consistency.md

The next prompt should cover embedding provider/model consistency and Chroma collection metadata fingerprinting only.

Do not execute Prompt002 in this run.
