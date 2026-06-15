# Prompt072 Raw Document Upload To Staging Vectorstore Workflow

Proceed autonomously.
Do not ask yes/no confirmation for safe local repository reads, lightweight checks, report generation, prompt generation, commits, or tags.

## Context

KuraDen is an on-prem/private internal document RAG chatbot for Japanese manufacturing companies.

Prompt071 found that `/admin/ingestion` is browser-connected but dry-run JSONL path validation only. Raw PDF/DOCX/XLSX/CSV/PPTX conversion exists in backend/CLI code, and staging vectorstore import exists in script/CLI paths, but a non-technical browser operator cannot upload raw documents and import them into an explicit non-production staging collection.

## Goal

Implement a safe browser-connected admin workflow that uploads raw PDF/DOCX/XLSX/CSV/PPTX documents, converts them to canonical chunks, validates the import manifest, and imports only into an explicit non-production staging collection after validation.

## Scope

- Extend admin ingestion backend and UI.
- Reuse existing converters and ingestion code where possible.
- Keep production/default vectorstore mutation refused.
- Preserve existing dry-run JSONL validation behavior.
- Store only safe job metadata in browser responses.

## Safety constraints

- no `.env`
- no secrets
- no real customer data
- no production/default vectorstore mutation
- unrelated orphan files untouched
- do not weaken tenant authorization
- do not weaken tenant isolation
- do not weaken API key behavior
- do not weaken OIDC/session behavior
- do not weaken RBAC behavior
- do not weaken rate limiting
- do not weaken `production_safe` behavior
- do not change retrieval thresholds
- do not change cross-encoder settings
- do not expose API keys, OIDC secrets, session secrets, trust tokens, raw prompts, raw document text, tenant-private data, customer-private data, or real identity data in reports, docs, tests, prompts, artifacts, logs, UI, or generated files

## Implementation requirements

- Add admin-gated upload endpoint for supported raw files.
- Accept only PDF, DOCX, XLSX, CSV, PPTX.
- Require expected tenant ID.
- Require explicit non-production staging collection for actual import.
- Provide dry-run conversion/manifest mode that does not mutate vectorstore.
- Provide execute mode that imports only after manifest is clean and staging collection passes non-production checks.
- Do not return raw document text in API responses.
- Add bounded summary fields: file names, counts, source types, chunk counts, issue counts, collection, mode, job id, status.
- Reuse `rag_core.document_converters.convert_file_to_canonical_chunks`.
- Reuse manifest validation from `scripts.import_manifest`.
- Reuse or factor ingestion logic from `scripts.ingest_canonical_jsonl` without shelling through unsafe commands.
- Keep existing `/admin/ingestion/dry-run` JSONL path endpoint compatible.
- Update `webapi/static/ingestion.html` with file upload controls and safe status display.

## Tests/checks

- Unit tests for upload endpoint auth.
- Unit tests that raw upload dry-run never calls vectorstore.
- Unit tests that execute requires explicit non-production collection.
- Unit tests that production/default collections are refused.
- Unit tests for each supported extension or representative fixture set.
- Tests that responses do not include raw document text or secrets.
- Tests that existing JSONL dry-run still passes.
- Run targeted tests plus `pytest --collect-only -q`.

## Required deliverables

- Product source changes for upload workflow.
- Tests.
- Report under `docs/reports/`.
- JSON artifact under `artifacts/commercial_readiness/`.

## Commit/tag expectations

Commit only if tests/checks pass and changes are scoped.

Expected commit message:
prompt072 raw document upload to staging vectorstore workflow

Expected tag:
prompt072-raw-document-upload-to-staging-vectorstore-workflow

## Final output format

- PASS/FAIL/PARTIAL
- commit hash if committed
- tag if created
- routes added/changed
- what works now
- safety checks
- tests run
- files changed
- blockers
