# Prompt072 Raw Document Upload To Staging Vectorstore Workflow

Task: `prompt072_raw_document_upload_to_staging_vectorstore_workflow`

Date: 2026-06-15

Final judgment: **PASS**

## Final behavior

Prompt072 adds a browser-connected, admin-gated raw document ingestion workflow using local document paths. It supports PDF, DOCX, XLSX, CSV, and PPTX where the existing converters support them. The workflow converts raw documents to canonical chunks, validates the import manifest, records a safe ingestion job summary, and optionally imports only into an explicitly named non-production staging collection.

Existing normalized JSONL dry-run validation is preserved at `/admin/ingestion/dry-run`.

New route:

- `POST /admin/ingestion/raw-documents`

Request fields:

- `inputs`: raw document paths, one or more
- `expected_tenant`: required tenant id
- `collection`: required explicit non-production staging collection
- `execute`: false for conversion/manifest dry-run, true for staging vectorstore import after a clean manifest

Responses include safe metadata only: file names, processed/skipped counts, generated chunk counts, source type counts, issue counts, target tenant, target collection, mode, status, and whether vectorstore was mutated. Responses do not include raw document text or chunk text.

## Browser usage steps

1. Open `/admin/ingestion` as an admin/operator.
2. Use the existing top section for normalized chunk JSONL dry-run validation.
3. Use the new `Raw文書 → ステージング取込` section for raw document paths.
4. Enter one local path per line for PDF/DOCX/XLSX/CSV/PPTX files.
5. Enter the required tenant ID.
6. Enter an explicit non-production staging collection name.
7. Leave the checkbox off to run conversion and manifest validation only.
8. Check `検証が通った場合にステージングベクトルストアへ実取込する` to import after validation.
9. Review the JSON status summary and job history.

## Supported input types

| Type | Status | Notes |
|---|---|---|
| CSV | Supported | Tested with synthetic CSV path. |
| XLSX | Supported by existing converter | Covered by existing converter tests. |
| DOCX | Supported by existing converter | Covered by existing converter tests. |
| PPTX | Supported by existing converter | Covered by existing converter tests. |
| PDF | Supported by existing converter/adapter | Depends on existing PDF adapter dependency availability; conversion failures are reported as bounded warnings. |

## Unsupported or partial input types

- Any extension outside PDF/DOCX/XLSX/CSV/PPTX is skipped with `unsupported_type`.
- Missing files are skipped with `missing_file`.
- Converter failures are skipped with `conversion_failed` and an error type only; raw text is not returned.
- If no supported chunks are generated, the job returns `issues_found` and does not mutate vectorstore.

## Files changed

- `scripts/ingest_canonical_jsonl.py`
- `webapi/ingestion_jobs.py`
- `webapi/main.py`
- `webapi/static/ingestion.html`
- `tests/test_ingestion_ui_job_status.py`
- `docs/reports/prompt072_raw_document_upload_to_staging_vectorstore_workflow.md`
- `artifacts/commercial_readiness/prompt072_raw_document_upload_to_staging_vectorstore_workflow.json`

## Tests/checks run

All test commands were run with a temporary no-`.env` dotenv stub so local `.env` contents were not read.

- `python -m py_compile webapi/ingestion_jobs.py webapi/main.py scripts/ingest_canonical_jsonl.py tests/test_ingestion_ui_job_status.py`
- `pytest tests/test_ingestion_ui_job_status.py -q` → 13 passed
- `pytest tests/test_multiformat_onboarding.py tests/test_safe_collection_promotion.py tests/test_embedding_fingerprint.py tests/test_document_converters.py -q` → 37 passed
- `pytest --collect-only -q` → 866 tests collected

Full suite was not run. In this sandbox, FastAPI `TestClient` hangs even for a minimal app, so endpoint tests in the touched ingestion test file were converted to direct route/helper coverage and broader full-suite execution was not reasonable.

## How to verify staging vectorstore mutation

Use `/admin/ingestion` raw document section with:

- a supported synthetic/local document path
- a non-empty tenant ID
- an explicit non-production collection such as `pilot_staging_v1`
- execute checkbox enabled

A successful import returns:

- `mode: raw_document_execute`
- `status: ok`
- `vectorstore_mutated: true`
- `ingested_chunks` greater than zero
- `collection` equal to the requested non-production staging collection

Production/default collection names are rejected before conversion/import.

## `/chat-ui` support

`/chat-ui` still cannot query a newly ingested staging collection from the browser. Prompt072 intentionally does not implement collection selection in chat. That remains Prompt073.

## Remaining blockers

- `/chat-ui` staging collection query selection is still missing.
- Browser file upload multipart support is not implemented; Prompt072 implements browser path-based raw document ingestion to avoid new dependencies.
- PDF support depends on the existing PDF adapter dependency availability in the runtime.
- Ingestion job registry is still in-memory.

## Next recommended prompt

Run `prompts/claude/product/prompt073_staging_collection_query_selection_for_chat_ui.md`.
