# Prompt011: Phase 4-A Tenant Isolation In The Retrieval Layer

You are working in:

/home/rai/chatbot

## Goal

Implement Phase 4-A: tenant isolation for retrieval so chunks from one tenant can never be retrieved for another. The default tenant must preserve current single-tenant behavior exactly.

This is the repo's own documented production blocker ("DB persistence and tenant isolation are not complete", docs/production_readiness_checklist.md).

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

1. Ingest: scripts/ingest_canonical_jsonl.py and scripts/pdf_to_canonical_jsonl.py already carry tenant metadata in some records (tenant_id fields exist in canonical rows). Ensure every ingested chunk's metadata has a tenant_id (default "default" when absent) — normalize at ingest time, do not rewrite existing files.

2. Retrieval filter (rag_core/retrieval.py):

- _build_base_where: add a tenant condition to the Chroma where clause. Backward compatibility: legacy chunks without tenant_id must still match for tenant "default" only. If Chroma's where syntax cannot express "missing or equal" in one clause, filter post-query in vector_retrieve for the default tenant and use a strict equality clause for non-default tenants.
- _meta_matches_where / keyword_retrieve: same tenant semantics for the BM25 path.
- expand_parent_chunks and add_neighbor_chunks must not leak cross-tenant parents/neighbors.

3. Thread tenant through the qa layer and /chat:

- answer_query / answer_query_with_trace / answer_query_stream / debug_retrieve_with_trace accept tenant_id (default "default").
- ChatRequest gains optional tenant_id (default "default") — additive, existing requests unchanged.
- The approved-QA lookup already takes tenant_id; pass the request value instead of hardcoding "default".
- Include tenant_id in audit events (already present in most) and in the answer-cache key (rag_core/answer_cache.py) so tenants never share cached answers.

4. Add targeted tests only for:

- default tenant retrieves legacy chunks (no tenant_id) and default-tagged chunks — current behavior preserved
- tenant A query never returns tenant B chunks (keyword and vector paths, fake collection)
- parent expansion does not pull a parent row from another tenant
- /chat with tenant_id reaches approved-QA lookup and cache key with that tenant
- cache: same question, different tenants → different cache entries

## Explicit non-goals

Do not implement these in this prompt:

- per-tenant collections or databases
- tenant authentication/authorization mapping (API keys to tenants)
- tenant management endpoints
- re-ingesting or migrating existing data
- changing guard/citations/streaming/cache semantics beyond the tenant key
- new dependencies
- broad refactors

## Constraints

- No new dependencies.
- Do not read or print .env.
- Do not expose secrets.
- Default behavior (no tenant_id sent, single-tenant data) must remain byte-identical: the deterministic eval smoke must pass unchanged.
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
  --output runs/eval/prompt011_smoke_check.json

If available and safe, run scripts/product_readiness_smoke.sh.

## Required final output

Report in this exact order:

1. Preconditions (repo path, branch, initial git status summary, relevant files found; verify Prompt010 is complete — metrics_registry wired and stage_latency_ms in trace — before implementing)
2. Implementation summary (files changed, exact behavior added, explicit non-goals preserved)
3. Verification results (targeted tests, collect-only, eval smoke, smoke script if run, any skipped verification and why)
4. Git diff summary (git diff --stat, no large diffs)
5. Final judgment: PASS / PARTIAL / FAIL, and whether it is safe to continue to Prompt012.
6. Next prompt file: if PASS, write exactly one next recommended prompt to prompts/claude/prompt012_phase4b_deployment_packaging.md covering deployment packaging only (Dockerfile, docker-compose.yml with a vectorstore volume, .env.example with placeholder keys only — never real values, and a minimal GitHub Actions workflow running the product readiness smoke test subset). Do not execute Prompt012 in this run.

Final clarification before execution:

Tenant compatibility:

- Preserve current single-tenant behavior through tenant_id="default".
- Existing requests without tenant_id must behave exactly as tenant_id="default".
- Legacy chunks without tenant_id metadata must be visible only to tenant_id="default".
- Non-default tenants must never see legacy untagged chunks.

Tenant normalization:

- Normalize missing, empty, or whitespace-only tenant_id to "default".
- Treat tenant_id as retrieval scope only, not as authentication or authorization.
- Do not add tenant management endpoints.
- Do not map API keys to tenants in this prompt.

Isolation boundary:

- Apply tenant filtering consistently to:
  - Chroma vector retrieval
  - BM25 / keyword retrieval
  - parent expansion
  - neighbor lookup
  - approved-QA lookup
  - answer-cache key
  - /chat
  - /chat/stream
  - debug retrieval paths
- Do not allow cross-tenant leakage through parent_id, source, document id, chunk id prefix, neighbor lookup, fallback paths, or debug traces.

Chroma where compatibility:

- Use a strict tenant_id equality filter for non-default tenants.
- For tenant_id="default", include both tenant_id="default" and legacy missing-tenant chunks.
- If Chroma cannot express missing-or-default safely in where syntax, retrieve with the safest available where clause and apply an explicit post-query metadata filter before returning results.
- Tests must cover both vector and keyword paths.

Ingest behavior:

- New ingest paths should stamp tenant_id into metadata, defaulting to "default" when absent.
- Do not rewrite, delete, or migrate existing corpus files.
- Existing untagged corpus must remain usable for the default tenant.

Cache and approved-QA:

- Include tenant_id in the answer-cache key.
- Cache entries must never be shared across tenants.
- Approved exact-match lookup must receive tenant_id from the request.
- Existing approved-QA behavior for default tenant must remain unchanged.

Response contract:

- Adding optional tenant_id to ChatRequest is allowed.
- Do not change existing response field sets.
- Deterministic eval smoke must remain 21/21 for default tenant.

Scope control:

- Do not change guard, citation validation, LLM generation, streaming protocol, API auth, CORS, metrics semantics, provider retry behavior, or cache semantics beyond adding tenant_id to the key.
- Do not add new dependencies.
- Keep changes minimal and localized.
- Tests must not require network access or an OpenAI API key.

Stop conditions:

- If tenant filtering cannot be implemented safely in both vector and keyword retrieval without a broad rewrite, stop and report PARTIAL.
- If parent expansion or neighbor lookup cannot be made tenant-safe with a minimal localized change, stop and report PARTIAL.

If any instruction conflicts, follow this Final clarification section.
