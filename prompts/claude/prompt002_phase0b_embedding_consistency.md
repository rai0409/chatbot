# Prompt002: Phase 0-B Embedding Provider/Model Consistency

You are working in:

/home/rai/chatbot

## Goal

Implement Phase 0-B: embedding provider/model consistency and Chroma collection metadata fingerprinting only.

Make it impossible to silently query a Chroma collection with a different embedding provider/model than the one used at ingest time, and unify the embedding provider default across modules.

This prompt must not change normal /chat or /search response contracts beyond clear error reporting on a real mismatch.

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

1. Embedding fingerprint at ingest: in scripts/ingest_canonical_jsonl.py, stamp the active embedding fingerprint into the Chroma collection metadata when ingesting. The fingerprint must include:

- embed_provider (from rag_core/embedding_provider.py provider name)
- embed_model (the resolved model name for that provider)

2. Fingerprint verification at query time: in rag_core/store.py (or a small helper next to it), compare the active provider/model fingerprint against the collection metadata stamp when the collection is opened for querying. On mismatch, raise a clear RuntimeError naming both fingerprints. If the collection has no stamp (legacy collections), do not fail; log a structured WARNING once per process and continue.

3. Unify the provider default: webapi/main.py _embedding_client currently defaults EMBED_PROVIDER to "openai" while rag_core/embedding_provider.py default_provider_name() defaults to "local". Make _embedding_client use embedding_provider.default_provider_name() / is_local_provider() so there is exactly one source of truth.

4. Expose the active embedding fingerprint (embed_provider, embed_model) in the existing /health endpoint payload. Only add fields.

5. Add targeted tests only for:

- ingest stamps the collection metadata fingerprint (use a fake/in-memory collection object, no network)
- query-time mismatch raises RuntimeError with both fingerprints in the message
- legacy unstamped collection warns once and does not fail
- /health includes embed_provider and embed_model
- webapi _embedding_client respects the unified default (no hardcoded "openai" default)

## Explicit non-goals

Do not implement these in this prompt:

- re-embedding or migrating existing vectors
- changing the default embedding model itself
- retrieval batching
- Chroma client singleton
- guard/no-answer changes
- citation changes
- streaming
- auth/rate limiting/CORS
- tenant isolation
- Docker/CI changes
- broad refactors

## Constraints

- No new dependencies.
- Do not read or print .env.
- Do not expose secrets.
- Do not change existing /chat or /search API contracts (a hard RuntimeError on true fingerprint mismatch is acceptable and intended).
- Only add fields to /health.
- Keep changes minimal and localized.
- Do not run full test suites unless targeted verification clearly requires it.
- Tests must not require network access or an OpenAI API key.
- Preserve current behavior for legacy unstamped collections (warn, do not fail).

## Verification

Run targeted tests first.

Then run:

python -m pytest --collect-only

If available and safe, run:

scripts/product_readiness_smoke.sh

## Required final output

Report in this exact order:

1. Preconditions (repo path, branch, initial git status summary, relevant files found)
2. Implementation summary (files changed, exact behavior added, explicit non-goals preserved)
3. Verification results (targeted tests, pytest collect-only, smoke if run, any skipped verification and why)
4. Git diff summary (git diff --stat, no large diffs)
5. Final judgment: PASS / PARTIAL / FAIL, and whether it is safe to continue to Prompt003.
6. Next prompt file: if PASS, write exactly one next recommended prompt to prompts/claude/prompt003_phase0c_retrieval_batching.md covering query-embedding batching (single embeddings call for base+augmented query, skip identical second pass) and the Chroma client singleton only. Do not execute Prompt003 in this run.
