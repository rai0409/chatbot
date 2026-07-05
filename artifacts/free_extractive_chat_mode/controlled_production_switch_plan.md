# Controlled Production Switch Plan

## Executive Summary
- Switch method: config-only runtime switch.
- Production overwrite: forbidden.
- Existing production collection: keep intact.
- Candidate collection: `chatbot_chunks_v1_aligned_candidate`.
- Rollback method: restore the previous collection name in environment configuration and restart/reload the service.
- This plan does not execute production promotion.

## Preconditions
- The four related commits are complete:
  - `Add free local extractive chat mode`
  - `Add free local extractive validation evidence`
  - `Add extractive mode commit plan and promotion gate`
  - `Add canonical metadata and fingerprint audit tools`
- Promotion decision report exists:
  - `artifacts/free_extractive_chat_mode/promotion_decision_report.md`
- Validation is green:
  - exact QA: `118/118`
  - unknown abstention: `32/32`
  - normal retrieval: `hybrid_hit@5=1.0`
- Candidate collection exists:
  - `chatbot_chunks_v1_aligned_candidate`
- Candidate collection alignment/fingerprint audit is green:
  - `artifacts/free_extractive_chat_mode/candidate_alignment_audit/`
- Previous production collection name must be recorded before switch.
- `.env` body and secret values must not be displayed in tickets, logs, reports, or chat.
- No vectorstore deletion, collection reset, ingestion reset, or production overwrite is allowed.

## Current Candidate
- Collection name: `chatbot_chunks_v1_aligned_candidate`
- Chunk count: `116`
- Keyword index records: `116`
- Canonical normalized JSONL: `index/chunks.canonical.normalized.jsonl`
- Embedding provider: `local`
- Embedding model: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- Embedding dimension: `384`
- `CHAT_GENERATION_MODE`: `extractive`
- Validation summary:
  - exact QA: `total_cases=118`, `errors=0`, `answer_match_rate=1.0`, `approved_exact_rate=1.0`
  - unknown abstention: `total_cases=32`, `errors=0`, `abstained_count=32`, `unsupported_answer_count=0`
  - normal retrieval: `hybrid_hit@5=1.0`, `still_failed=[]`
  - manual `/chat` smoke: HTTP `200`, non-empty fallback, `used_fallback=true`

## Switch Method
Set runtime environment to:
- `CHROMA_COLLECTION=chatbot_chunks_v1_aligned_candidate`
- `CHAT_GENERATION_MODE=extractive`
- `EMBED_PROVIDER=local`
- `LOCAL_EMBED_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`

Then restart or reload the service using the normal production deployment mechanism.

Do not:
- delete the previous production collection
- overwrite the previous production collection
- reset Chroma collections
- run ingestion/reset
- edit or display `.env` secret values

## Exact Environment Variables
Required non-secret variables:

```text
CHROMA_COLLECTION=chatbot_chunks_v1_aligned_candidate
CHAT_GENERATION_MODE=extractive
EMBED_PROVIDER=local
LOCAL_EMBED_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
```

OpenAI chat completion API key:
- `OPENAI_API_KEY` is not required for `CHAT_GENERATION_MODE=extractive`.
- Do not print or disclose any existing `OPENAI_API_KEY` value.
- Do not rely on OpenAI chat completion for this switch.

Optional deployment variables:
- `HOST`: service bind host, deployment-specific.
- `PORT`: service bind port, deployment-specific.
- `APPROVED_QA_ENABLED` / `APPROVED_QA_PATH`: keep aligned with the existing production approved-QA setup.
- `API_AUTH_ENABLED` / auth-related variables: keep aligned with existing production policy; do not disclose secret values.

## Start Command Example
Local non-secret example:

```bash
export CHROMA_COLLECTION=chatbot_chunks_v1_aligned_candidate
export CHAT_GENERATION_MODE=extractive
export EMBED_PROVIDER=local
export LOCAL_EMBED_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
.venv/bin/uvicorn webapi.main:app --host 127.0.0.1 --port 8010
```

systemd / production concept example:

```text
Environment=CHROMA_COLLECTION=chatbot_chunks_v1_aligned_candidate
Environment=CHAT_GENERATION_MODE=extractive
Environment=EMBED_PROVIDER=local
Environment=LOCAL_EMBED_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
ExecStart=<existing service start command>
```

Keep any secret-bearing environment files out of reports and chat transcripts.

## Health Check
After restart/reload, check `/health`.

Required:
- `status=ok`
- `keyword_index_loaded=true`
- `keyword_index_records=116`
- `chroma_collection=chatbot_chunks_v1_aligned_candidate`
- `embed_provider=local`
- `embed_model=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- `chat_generation_mode=extractive`

Also verify:
- `vectorstore_collection_name` is expected for the deployed config.
- `keyword_index_path` points to the expected corpus path.
- no secret values appear in health output.

## Smoke Test
Run smoke tests before broad traffic exposure.

Approved exact sample:
- Use a known approved exact question.
- Expected:
  - HTTP `200`
  - `answer_mode=approved_exact_match`
  - `retrieval_source=approved_qa_exact`
  - answer text matches approved answer
  - citations are present

Unknown sample:
- Use `unknown_006`:
  - `大阪府の電子入札システムでJava Plug-in警告が出る原因は何ですか。`
- Expected:
  - HTTP `200`
  - non-empty answer
  - explicit fallback/abstention
  - no unsupported definitive answer

Normal retrieval sample:
- Use a known normal retrieval question from the 32-case normal retrieval set.
- Expected:
  - expected source document appears in top retrieved evidence
  - no `/chat` 500

Manual `/chat` smoke:
- Confirm payload includes:
  - `answer_text`
  - `answer_mode`
  - `chat_generation_mode=extractive`
  - citations when grounded, or explicit fallback when not grounded

Log check:
- Check startup logs and request logs.
- No repeated vectorstore, keyword index, auth, or retrieval errors.
- No accidental OpenAI chat completion errors in extractive mode.

No API key mode check:
- Confirm `/chat` works with no OpenAI chat completion key requirement.
- Do not print or expose any key value.

## Rollback Method
Rollback is config-only.

Set:

```bash
export CHROMA_COLLECTION=<previous_collection_name>
```

Then restart or reload the service using the normal production deployment mechanism.

Rollback checks:
- `/health` returns `status=ok`.
- `/health` reports `<previous_collection_name>`.
- keyword index remains loaded.
- approved exact sample passes.
- unknown sample abstains safely.
- normal retrieval sample passes.
- logs show no critical startup/request errors.

## Forbidden Actions
- Production collection overwrite.
- Collection delete.
- Vectorstore delete.
- Collection reset.
- Ingestion reset.
- Evaluator relaxation.
- Empty-answer abstention counting.
- Unsupported file type support claim.
- LLM quality support claim.
- Secret/API key disclosure.
- `.env` secret value disclosure.

## Operator Checklist
Switch前:
- Record previous production collection name.
- Confirm candidate collection name: `chatbot_chunks_v1_aligned_candidate`.
- Confirm promotion decision report exists.
- Confirm latest validation summary is green.
- Confirm rollback owner and rollback window.
- Confirm no production overwrite/reset/delete is planned.

Switch中:
- Apply non-secret environment variable changes.
- Restart/reload service.
- Do not run ingestion/reset.
- Do not delete or mutate vectorstore collections.
- Capture `/health`.

Switch後:
- Run health check.
- Run approved exact sample.
- Run unknown sample.
- Run normal retrieval sample.
- Run manual `/chat` smoke.
- Check logs.
- Record switch result and artifact paths.

Rollback時:
- Restore `CHROMA_COLLECTION=<previous_collection_name>`.
- Restart/reload service.
- Run rollback health check.
- Run rollback smoke tests.
- Preserve failure evidence for analysis.

## Success Criteria
- `/health` green.
- `keyword_index_loaded=true`.
- `keyword_index_records=116`.
- active collection is `chatbot_chunks_v1_aligned_candidate`.
- approved exact sample is green.
- unknown sample abstains safely.
- retrieval sample is green.
- `/chat` returns no 500s in smoke tests.
- logs show no critical repeated errors.
- rollback path remains available.

## Failure Criteria
- `/health` abnormal.
- keyword index not loaded.
- active collection mismatch.
- `/chat` returns 500.
- unknown sample produces unsupported answer.
- retrieval sample misses expected source.
- OpenAI chat completion key becomes required in extractive mode.
- rollback cannot be performed by restoring the previous collection variable.

## Next Step After Switch
- Run grounded extractive answer quality evaluation.
- Design and verify PDF upload E2E.
- Treat DOCX/CSV/XLSX/PPTX as separate future evaluation scope.
- Keep LLM mode quality evaluation as a separate gate.
