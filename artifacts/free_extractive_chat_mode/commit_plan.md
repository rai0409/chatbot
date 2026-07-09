# Commit Plan

## Status
- Commit preparation only.
- `git commit` has not been run.
- `git push` has not been run.
- No vectorstore deletion, collection reset, production overwrite, or ingestion/reset operation was performed.

## Diff Classification

### Runtime / Config
- `config.py`
  - Adds `CHAT_GENERATION_MODE`.
  - Adds `resolve_chat_generation_mode()`.
  - Adds candidate-aware Chroma collection resolution.
- `.env.example`
  - Documents free/local-only extractive mode.
  - Documents candidate collection override.

### QA / Extractive Fallback
- `rag_core/qa.py`
  - Adds extractive answer construction.
  - Adds conservative evidence checks.
  - Adds non-empty abstention fallback.
  - Adds LLM-unavailable fallback behavior.

### API / Health
- `webapi/main.py`
  - Skips OpenAI chat client creation in extractive mode.
  - Exposes `chat_generation_mode` and collection metadata in `/health`.
  - Returns `answer_mode` and `chat_generation_mode` in `/chat`.
  - Preserves `retrieval_source=approved_qa_exact` for approved exact payloads.

### Vectorstore / Collection Metadata
- `rag_core/store.py`
  - Uses shared collection resolution.
- `scripts/ingest_canonical_jsonl.py`
  - Stamps collection fingerprint metadata when source JSONL path is available.
- Related untracked support candidates:
  - `rag_core/canonical_metadata.py`
  - `rag_core/embedding_fingerprint.py`
  - `tools/audit_corpus_alignment.py`
  - `tools/build_normalized_canonical_chunks.py`
  - `tools/stamp_chroma_collection_fingerprint.py`
  - `tests/test_canonical_metadata.py`
  - `tests/test_embedding_fingerprint_metadata.py`
  - `index/chunks.canonical.normalized.jsonl`

### Evaluation Scripts
- `tools/evaluate_unknown_abstention.py`
  - Treats non-200 `/chat` responses as evaluator errors.
- `scripts/run_free_extractive_chat_mode_check.sh`
  - Fixed validation entrypoint for free/local-only extractive mode.

### README
- `README.md`
  - Adds free/local-only mode instructions.
  - Adds a short reference to the candidate collection promotion gate.

### Artifacts
- `artifacts/free_extractive_chat_mode/`
  - Validation evidence and final reports.

## Commit 1: Code / Docs / Script Changes

Message:

```text
Add free extractive chat mode
```

Commit target candidates:
- `.env.example`
- `README.md`
- `config.py`
- `rag_core/qa.py`
- `rag_core/store.py`
- `webapi/main.py`
- `tools/evaluate_unknown_abstention.py`
- `scripts/ingest_canonical_jsonl.py`
- `scripts/run_free_extractive_chat_mode_check.sh`

Conditionally include with Commit 1 if the promotion/fingerprint support is intended to land in the same PR:
- `rag_core/canonical_metadata.py`
- `rag_core/embedding_fingerprint.py`
- `tools/audit_corpus_alignment.py`
- `tools/build_normalized_canonical_chunks.py`
- `tools/stamp_chroma_collection_fingerprint.py`
- `tests/test_canonical_metadata.py`
- `tests/test_embedding_fingerprint_metadata.py`
- `index/chunks.canonical.normalized.jsonl`

Reason:
- These files implement and document free/local-only extractive mode.
- They also provide the collection metadata/fingerprint support needed to make candidate collection evaluation auditable.

## Commit 2: Validation Evidence Artifacts

Message:

```text
Add free extractive chat mode validation evidence
```

Commit target candidates:
- `artifacts/free_extractive_chat_mode/commit_plan.md`
- `artifacts/free_extractive_chat_mode/final_diff_audit.md`
- `artifacts/free_extractive_chat_mode/final_status.md`
- `artifacts/free_extractive_chat_mode/free_extractive_chat_mode_report.md`
- `artifacts/free_extractive_chat_mode/health.json`
- `artifacts/free_extractive_chat_mode/promotion_gate.md`
- `artifacts/free_extractive_chat_mode/unknown_006_manual_chat_response.json`
- `artifacts/free_extractive_chat_mode/validation_summary.json`
- `artifacts/free_extractive_chat_mode/chat_exact_qa/`
- `artifacts/free_extractive_chat_mode/unknown_abstention/`
- `artifacts/free_extractive_chat_mode/normal_retrieval_candidate/`

Reason:
- These files are evidence, not runtime code.
- Keeping them separate makes review easier and allows code changes to be reviewed independently from generated validation outputs.

## Commit対象外候補
- `.env`
- local cache directories
- virtualenv files
- vectorstore database files
- secret-bearing files
- transient server logs unless the reviewer explicitly wants them
- `artifacts/free_extractive_chat_mode/uvicorn.log` unless runtime logs are required as evidence

Reason:
- These are environment-specific, potentially noisy, or may contain local operational details.
- The required validation evidence is already captured in structured JSON/CSV/Markdown files.

## 注意点
- Do not commit secrets or API keys.
- Do not commit a production collection overwrite.
- Do not run ingestion/reset as part of commit preparation.
- Do not relax evaluators to make gates pass.
- Preserve the verified free/local-only validation conditions.
- Confirm whether the untracked fingerprint/canonical metadata support files should be included in Commit 1 before a human performs the actual commit.

## Not Executed
- `git commit`: not executed.
- `git push`: not executed.
