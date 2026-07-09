# Prompt073 Staging Collection Query Selection For Chat UI

Task: `prompt073_staging_collection_query_selection_for_chat_ui`

Date: 2026-06-17

Final judgment: **PASS**

## Final behavior

`/chat-ui` and the chat/search backend can now query an explicit Prompt072-created non-production staging collection without changing default served-collection behavior.

Default behavior is unchanged when `staging_collection` is absent:

- `/chat` and `/chat/stream` still use the configured served/default collection.
- approved-QA exact match, answer cache, citations, abstain/no-answer behavior, feedback tokens, thread APIs, tenant profile limits, metrics, and rate limiting remain active on the default path.
- `/search` still works with no tenant/collection fields and normalizes to the default tenant.

Staging behavior is explicit:

- request field: `staging_collection`
- tenant field: `tenant_id`
- staging collection names must be non-empty when used, safe ASCII identifiers, and non-production/non-default.
- production/default collection names are rejected for staging query mode.
- the selected staging collection must be present in the Prompt072 in-memory job registry as a successful `raw_document_execute` job for the same tenant with `vectorstore_mutated=true`.
- tenant authorization is enforced before staging collection validation/retrieval.
- wrong-tenant collections return `403`; unknown collections return `404`; known but not executed/not ready collections return `409`.
- staging queries use existing-only vectorstore access (`get_collection`) and never create missing collections during query.
- staging answer retrieval uses vector retrieval from the selected collection and skips the default JSONL keyword/parent index to avoid mixing served/default corpus data into staging results.

## Browser usage steps

1. Use Prompt072 to import a supported synthetic/local document into a non-production staging collection with `execute=true`.
2. Open `/chat-ui`.
3. Open `接続設定`.
4. Enter the API key if API auth is enabled.
5. Enter the authorized tenant ID.
6. If your UI role is `admin` or `operator`, enter the Prompt072 staging collection name in `ステージングコレクション（非本番）`.
7. Confirm the visible scope badges show:
   - `tenant: <tenant_id>`
   - `collection: staging <collection>`
8. Submit a question. Each turn displays the tenant and collection used for that query.
9. Clear the staging collection field to return to `collection: served default`.

The staging collection input is hidden unless `/ui/context` reports `admin` or `operator`. Backend enforcement does not depend on this cosmetic UI gate.

## API examples

Default chat behavior:

```bash
curl -s -X POST http://127.0.0.1:8000/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"question":"質問です","tenant_id":"default"}'
```

Staging chat:

```bash
curl -s -X POST http://127.0.0.1:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"question":"ステージング文書について","tenant_id":"tenant_a","staging_collection":"tenant_a_stage_v1"}'
```

Staging search:

```bash
curl -s -X POST http://127.0.0.1:8000/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"ステージング文書について","tenant_id":"tenant_a","staging_collection":"tenant_a_stage_v1"}'
```

Staging debug search:

```bash
curl -s -X POST http://127.0.0.1:8000/search/debug \
  -H 'Content-Type: application/json' \
  -d '{"query":"ステージング文書について","tenant_id":"tenant_a","staging_collection":"tenant_a_stage_v1","generate_answer":false}'
```

When API auth is enabled, include the existing runtime auth headers. No API keys or secrets are stored in the page.

## Verifying Prompt072-ingested staging retrieval

1. In `/admin/ingestion`, use the raw-document section.
2. Enter a supported local synthetic document path.
3. Enter the target tenant, for example `tenant_a`.
4. Enter a non-production staging collection, for example `tenant_a_stage_v1`.
5. Enable execute and submit.
6. Confirm the job response has:
   - `mode: raw_document_execute`
   - `status: ok`
   - `vectorstore_mutated: true`
   - `collection: tenant_a_stage_v1`
   - `expected_tenant: tenant_a`
7. Open `/chat-ui`, enter the same tenant and staging collection, and ask a question whose answer is present in that synthetic document.
8. The response payload/stream final event includes:
   - `query_collection_mode: staging`
   - `query_collection: tenant_a_stage_v1`

## Files changed

- `rag_core/store.py`
- `rag_core/retrieval.py`
- `rag_core/qa.py`
- `webapi/api_auth.py`
- `webapi/ingestion_jobs.py`
- `webapi/main.py`
- `webapi/static/chat.html`
- `tests/test_staging_collection_query_selection.py`
- `docs/reports/prompt073_staging_collection_query_selection_for_chat_ui.md`
- `artifacts/commercial_readiness/prompt073_staging_collection_query_selection_for_chat_ui.json`

## Tests/checks run

All commands were run without reading `.env`.

- `python -m py_compile rag_core/store.py rag_core/retrieval.py rag_core/qa.py webapi/api_auth.py webapi/ingestion_jobs.py webapi/main.py tests/test_staging_collection_query_selection.py` -> passed
- `PYTHONPATH=. uv run pytest tests/test_staging_collection_query_selection.py -q` -> 8 passed
- `PYTHONPATH=. uv run pytest tests/test_commercial_chat_workspace_ui.py tests/test_enduser_chat_ui.py tests/test_chat_stream.py tests/test_chat_tenant_product_profile_runtime.py tests/test_api_key_tenant_authorization.py -q` -> 54 passed
- `PYTHONPATH=. uv run pytest tests/test_ingestion_ui_job_status.py tests/test_safe_collection_promotion.py tests/test_tenant_isolation.py -q` -> 27 passed
- `PYTHONPATH=. uv run pytest tests/test_api_auth.py tests/test_rate_limit.py -q` -> 31 passed
- `PYTHONPATH=. uv run pytest --collect-only -q` -> 874 tests collected
- `PYTHONPATH=. uv run pytest -q` -> 874 passed

`pytest` was not on PATH directly; tests were run via `uv run pytest`.

## Prompt074 and Prompt075 remaining work

- Browser multipart file upload is still not implemented.
- Ingestion jobs are still in-memory.
- Staging collection query allowlisting is therefore process-local and depends on Prompt072 jobs recorded in the current process.
- No full operator UX redesign was implemented.
- No production/default collection promotion or mutation workflow was changed.

## Remaining blockers

- A staging collection imported outside the Prompt072 in-memory job path is intentionally not queryable through `staging_collection` until a durable allowlist/discovery mechanism exists.
- Parent expansion and keyword retrieval for staging collections are intentionally skipped because Prompt072 staging imports write Chroma collections but do not create a matching staging JSONL keyword/parent index.
