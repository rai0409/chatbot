# Prompt071 Strict Actual Usage Ingestion And Retrieval Gap Audit

Task: `prompt071_strict_actual_usage_ingestion_and_retrieval_gap_audit`

Date: 2026-06-15

Scope: repo evidence only. No `.env` contents were read. No Docker, deployment, production/default vectorstore mutation, real customer data, or product runtime behavior changes were performed.

## 1. Executive judgment

| Question | Judgment | Evidence |
|---|---|---|
| Can the app be opened locally? | Implemented as FastAPI app; local server not started in this audit. `/health` route exists and manual context says it returns `status: ok`. | `webapi/main.py:70`, `webapi/main.py:866` |
| Can `/chat-ui` be opened? | Yes, browser route exists and serves static chat UI. | `webapi/main.py:939`, `webapi/static/chat.html` |
| Can `/admin/ingestion` be opened? | Yes, admin-gated browser route exists and serves dry-run ingestion UI. | `webapi/main.py:1089`, `webapi/static/ingestion.html` |
| Can `/admin/review` be opened? | Yes, admin-gated browser route exists and serves review queue UI. | `webapi/main.py:1019`, `webapi/static/review_queue.html` |
| Can normalized chunk JSONL be dry-run validated? | Yes, browser and backend dry-run validate JSONL paths through import manifest checks. | `webapi/main.py:1099`, `webapi/ingestion_jobs.py`, `scripts/import_manifest.py`, `tests/test_ingestion_ui_job_status.py` |
| Can normalized chunk JSONL be actually imported? | Yes, script/CLI-only. `scripts/ingest_canonical_jsonl.py` writes to Chroma/vectorstore and accepts `--collection`; not browser-connected. | `scripts/ingest_canonical_jsonl.py`, `tests/test_embedding_fingerprint.py` |
| Can raw Excel/Word/PDF be uploaded from the browser today? | No. Search found no `UploadFile`, multipart, `File(...)`, `Form(...)`, or `input type=file` in web UI/routes. | `rg UploadFile...`, `webapi/static/ingestion.html` |
| Can raw documents be converted and embedded today? | Converted: yes, backend/CLI for PDF/DOCX/XLSX/CSV/PPTX. Embedded: yes via CLI import after canonical JSONL exists. Browser-connected end-to-end: no. | `rag_core/document_converters/*`, `scripts/convert_document_to_canonical_jsonl.py`, `scripts/onboard_documents_dry_run.py`, `scripts/ingest_canonical_jsonl.py` |
| Can a newly ingested staging collection be queried from `/chat-ui` today? | No browser/operator selector exists. `/chat-ui` posts to `/chat/stream`, which uses `store.get_vectorstore()` with configured default collection only. Querying a staging collection would require runtime environment/config change or code/script path, not browser UI. | `webapi/static/chat.html`, `webapi/main.py:1339`, `rag_core/retrieval.py`, `rag_core/store.py` |

Actual usability label: **PARTIAL**

Reason: the app has usable UI surfaces and browser JSONL dry-run validation, plus CLI conversion/import tooling, but the non-technical raw Excel/Word/PDF upload-to-staging-vectorstore-to-chat flow is not browser-connected.

## 2. Current user-facing flows

| Route | Browser URL | Who uses it | What it does | What it does not do | Evidence path |
|---|---|---|---|---|---|
| `/chat-ui` | `http://<host>/chat-ui` | End user / operator | Serves static KuraDen chat workspace; sends questions to `/chat/stream`; supports runtime API key field and tenant ID field; shows citations and feedback controls. | Does not upload documents; does not choose vectorstore collection; does not expose staging collection selection; does not build or mutate index. | `webapi/main.py:939`, `webapi/static/chat.html` |
| `/admin/ingestion` | `http://<host>/admin/ingestion` | Admin/operator | Serves dry-run JSONL validation UI; accepts JSONL paths, expected tenant, optional non-production collection label; calls `/admin/ingestion/dry-run`; shows job history. | Does not accept file upload; does not accept raw PDF/DOCX/XLSX/CSV/PPTX uploads; does not convert documents; does not import to Chroma; explicitly says vectorstore is not changed. | `webapi/main.py:1089`, `webapi/static/ingestion.html`, `webapi/ingestion_jobs.py` |
| `/admin/review` | `http://<host>/admin/review` | Admin/reviewer | Serves review queue UI; supports review item listing and action endpoint. | Does not ingest documents or promote collections. | `webapi/main.py:1019`, `webapi/static/review_queue.html`, `tests/test_review_queue_page.py` |
| `/docs` | `http://<host>/docs` | Developer/operator | FastAPI Swagger UI route exists. OpenAPI schema shows `/admin/ingestion/dry-run` request body is `application/json` using `IngestionDryRunRequest`. | Does not provide a product upload workflow; schema has no multipart ingestion endpoint. | Dynamic no-`.env` OpenAPI inspection; `webapi/main.py` |
| `/metrics` | `http://<host>/metrics` | Operator/SRE | Returns process counters as JSON or Prometheus text with `?format=prometheus`. | Does not report ingestion pipeline completion or collection selector state beyond current counters. | `webapi/main.py:910`, `tests/test_metrics_observability.py` |
| `/auth/oidc/login` | `http://<host>/auth/oidc/login` | Enterprise user/operator when OIDC enabled | Default-off OIDC login route; returns 404 if OIDC is disabled; redirects through Authorization Code + PKCE when enabled. | Does not affect ingestion capability; no document flow. | `webapi/main.py:958`, `tests/test_oidc_login_session.py` |

## 3. Ingestion and retrieval pipeline evidence

| Stage | Status | Entrypoint | Tests | UI connected? | Risk |
|---|---|---|---|---|---|
| Raw file input | Partial | CLI accepts paths/directories; browser has no file input or multipart route. | `tests/test_multiformat_onboarding.py`, `tests/test_document_converters.py` | No | Non-technical operator cannot upload Excel/Word/PDF from browser. |
| Converter | Implemented | `rag_core.document_converters.convert_file_to_canonical_chunks`; `scripts/convert_document_to_canonical_jsonl.py`; `scripts/onboard_documents_dry_run.py` | `tests/test_document_converters.py` | No | Converter quality on real customer documents is not proven; synthetic-tested only. |
| Canonical chunks | Implemented | Converter outputs canonical JSONL; `rag_core/chunking_ja.py`; `scripts/approved_qa_to_canonical_jsonl.py`; `scripts/contract_ingest_json_to_canonical_jsonl.py` | `tests/test_chunking_ja.py`, `tests/test_document_converters.py`, `tests/test_contract_ingest_json_to_canonical_jsonl.py` | No for raw docs; Yes only as JSONL path validation input | Canonical output exists, but browser does not generate it. |
| Dry-run validation | Implemented | `/admin/ingestion/dry-run`; `webapi.ingestion_jobs.run_dry_run`; `scripts.import_manifest.build_manifest` | `tests/test_ingestion_ui_job_status.py`, `tests/test_multiformat_onboarding.py` | Yes | Dry-run only; job registry is in-memory. |
| Actual staging import | Implemented script/CLI-only | `scripts/ingest_canonical_jsonl.py`; `scripts/onboard_documents_dry_run.py --execute --collection <nonprod>` | `tests/test_embedding_fingerprint.py`; `tests/test_multiformat_onboarding.py` uses monkeypatched ingest call for execute path | No | Can mutate a non-production collection if run manually; not browser-connected; production/default refusal is stronger in onboarding script than bare ingest script. |
| Embedding | Implemented | `rag_core/embedder.py`, `rag_core/embedding_provider.py`, `scripts/ingest_canonical_jsonl.py` | `tests/test_embedding_provider.py`, `tests/test_embedding_fingerprint.py`, `tests/test_retrieval_batching.py` | No | Local/OpenAI provider setup is environment-dependent; real-environment not tested here. |
| Vectorstore write | Implemented script/CLI-only | Chroma `upsert`/`add` in `scripts/ingest_canonical_jsonl.py`; `rag_core/store.py` | `tests/test_embedding_fingerprint.py`, `tests/test_durable_multitenant_persistence.py` | No | Bare ingest can target configured/default collection if operator omits `--collection`; onboarding execute requires explicit non-production collection. |
| Keyword index write/load | Partial | Load: `rag_core/retrieval._load_keyword_index()` from `config.CHUNKS_JSONL_PATH`; build/write: `scripts/build_canonical_index.py` | `tests/test_keyword_index_status.py`, `tests/test_build_canonical_index.py` | No | Manual observed `/health` has `keyword_index_loaded=false`, `keyword_index_records=0`; vectorstore import does not automatically update keyword JSONL. |
| Safe collection promotion | Planning-only | `webapi/collection_promotion.py`; `scripts/promote_collection.py` | `tests/test_safe_collection_promotion.py` | No | It is a gate/report, not a live promotion UI or mutation path. |
| Chat/search query | Implemented for configured collection | `/chat`, `/chat/stream`, `/search`, `/search/debug`; `rag_core.retrieval.vector_retrieve`; `rag_core.store.get_vectorstore()` | `tests/test_chat_stream.py`, `tests/test_commercial_chat_workspace_ui.py`, `tests/test_tenant_isolation.py` | Yes for chat; No for collection selection | `/chat-ui` cannot select newly ingested staging collection; `/search` lacks tenant field; `/search/debug` calls retrieval without tenant_id in current code path. |

## 4. Supported input type matrix

| Type | Converter exists? | Tested? | Browser UI accepts? | CLI/script accepts? | Dry-run accepts? | Staging import accepts? | Embedding/vectorstore write tested? | Chat query verified? | Evidence path |
|---|---|---|---|---|---|---|---|---|---|
| JSONL | Not a raw converter; canonical format supported | Yes | Yes, as path text only | Yes | Yes | Yes, script/CLI-only | Yes, synthetic/fake and Chroma persistence tests | Not verified for newly ingested staging collection from `/chat-ui` | `webapi/static/ingestion.html`, `scripts/import_manifest.py`, `scripts/ingest_canonical_jsonl.py`, `tests/test_ingestion_ui_job_status.py` |
| PDF | Yes | Yes, adapter test requires PyMuPDF | No | Yes | After conversion only | After conversion only | Ingest path tested generically, not PDF-specific live staging-to-chat | Not verified | `rag_core/document_converters/pdf_adapter.py`, `tests/test_document_converters.py` |
| DOCX | Yes | Yes | No | Yes | After conversion only | After conversion only | Ingest path tested generically, not DOCX-specific live staging-to-chat | Not verified | `rag_core/document_converters/docx_converter.py`, `tests/test_document_converters.py` |
| XLSX | Yes | Yes | No | Yes | After conversion only | After conversion only | Ingest path tested generically, not XLSX-specific live staging-to-chat | Not verified | `rag_core/document_converters/xlsx_converter.py`, `tests/test_document_converters.py` |
| CSV | Yes | Yes | No | Yes | After conversion only | After conversion only | Keyword retrieval compatibility tested; ingest path tested generically | Not verified through `/chat-ui` staging | `rag_core/document_converters/csv_converter.py`, `tests/test_document_converters.py` |
| PPTX | Yes | Yes | No | Yes | After conversion only | After conversion only | Ingest path tested generically, not PPTX-specific live staging-to-chat | Not verified | `rag_core/document_converters/pptx_converter.py`, `tests/test_document_converters.py` |

## 5. Actual gap list

P0: required to use from browser with Excel/Word/PDF

- Missing browser raw document upload: no `UploadFile`, multipart route, or `input type=file`.
- Missing browser-connected convert → canonical chunks → dry-run → staging import workflow.
- Missing browser-visible staging import status that represents actual vectorstore mutation.
- Missing browser collection selector or staging query mode for `/chat-ui`.

P1: required for commercial operator usability

- Admin ingestion page text is honest but limited to JSONL dry-run; it does not guide non-technical raw document onboarding.
- No browser UX for previewing conversion summary, manifest issues, chunk counts, or source type counts before import.
- No browser UX for safe promotion; current promotion is planning-only CLI/module.
- Keyword JSONL index is not automatically built/loaded after staging import; `/health` can show `keyword_index_loaded=false`.

P2: required for robust customer PoC

- End-to-end raw document to answer is not verified through browser and `/chat-ui` against a newly ingested staging collection.
- Converter tests are strong synthetic tests, but real customer environment and messy customer documents are not tested here.
- Staging import and Chroma persistence tests use synthetic data and monkeypatch/fake collection paths for some checks.
- `/search` and `/search/debug` do not expose a browser/operator collection selection path; `/search` has no tenant field in request schema.

P3: future enhancements

- Durable job store for ingestion history instead of in-memory job registry.
- Rich document preview and sampled chunk preview with safe redaction.
- Browser promotion approval workflow tied to backup/restore checkpoints.
- More detailed metrics for ingestion stages and collection health.

## 6. Recommended next prompts

Generated because audit shows they are needed:

- `prompts/claude/product/prompt072_raw_document_upload_to_staging_vectorstore_workflow.md`
- `prompts/claude/product/prompt073_staging_collection_query_selection_for_chat_ui.md`
- `prompts/claude/product/prompt074_operator_ingestion_promotion_chat_ui_ux_hardening.md`
- `prompts/claude/product/prompt075_end_to_end_raw_document_to_answer_demo_gate.md`

## 7. Immediate next action

Immediate next prompt to run: **prompt072_raw_document_upload_to_staging_vectorstore_workflow.md**

Reason: raw document browser upload is missing.

## Required checks run

- `git log --oneline --decorate -20`
- `git tag --points-at HEAD`
- `git status --short`
- FastAPI route listing via no-`.env` import stub
- OpenAPI schema inspection for `/admin/ingestion*` via no-`.env` import stub
- `sed` inspection of `webapi/main.py`
- `sed` inspection of `webapi/static/ingestion.html`
- `sed` inspection of `webapi/static/chat.html`
- Search for `UploadFile`, multipart, `File(`, `Form(`, `input type=file`
- Search for PDF/DOCX/XLSX/CSV/PPTX converters and canonical JSONL paths
- Search/read of embedding, Chroma/vectorstore write, keyword index, collection selection logic
- Inspection of ingestion, converter, collection promotion, keyword, retrieval, and chat-related tests
- Verification that Prompt039, Prompt054, and Prompt070 reports exist
- `pytest --collect-only -q` with no-`.env` dotenv stub: 860 tests collected

Full test suite was not run.

## Notes on `.env` safety

`config.py` imports `dotenv.load_dotenv(BASE_DIR / ".env", override=True)`. Dynamic app import and pytest collection were therefore run with a temporary `/tmp/prompt071_no_dotenv/dotenv.py` stub that returns no values. The audit did not read `.env`.
