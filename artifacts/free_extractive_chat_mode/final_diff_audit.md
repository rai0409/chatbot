# Final Diff Audit

## Commands Audited
- `git status --short`
- `git diff --stat`
- `git diff -- config.py`
- `git diff -- rag_core/qa.py`
- `git diff -- rag_core/store.py`
- `git diff -- webapi/main.py`
- `git diff -- tools/evaluate_unknown_abstention.py`
- `git diff -- scripts/ingest_canonical_jsonl.py`
- `git diff -- .env.example`

## Working Tree Summary
- Modified tracked files:
  - `.env.example`
  - `README.md`
  - `config.py`
  - `rag_core/qa.py`
  - `rag_core/store.py`
  - `scripts/ingest_canonical_jsonl.py`
  - `tools/evaluate_unknown_abstention.py`
  - `webapi/main.py`
- New validation script:
  - `scripts/run_free_extractive_chat_mode_check.sh`
- Existing untracked support/evidence files observed:
  - `index/chunks.canonical.normalized.jsonl`
  - `rag_core/canonical_metadata.py`
  - `rag_core/embedding_fingerprint.py`
  - `tests/test_canonical_metadata.py`
  - `tests/test_embedding_fingerprint_metadata.py`
  - `tools/audit_corpus_alignment.py`
  - `tools/build_normalized_canonical_chunks.py`
  - `tools/stamp_chroma_collection_fingerprint.py`

## File-by-file Audit
- `.env.example`: documents `CHAT_GENERATION_MODE=extractive` and candidate collection override. Relevant to free/local-only operation.
- `config.py`: adds extractive chat mode resolution and Chroma collection resolution. Relevant and required.
- `rag_core/qa.py`: adds extractive evidence selection, grounded extractive answer building, abstention fallback, and LLM unavailable fallback. Relevant and required.
- `rag_core/store.py`: uses shared Chroma collection resolution. Relevant to candidate collection/local test behavior.
- `webapi/main.py`: exposes health metadata, avoids OpenAI client creation in extractive mode, returns mode fields, and preserves `retrieval_source=approved_qa_exact`. Relevant and required.
- `tools/evaluate_unknown_abstention.py`: makes non-200 `/chat` responses fail the evaluator instead of being silently parsed. Relevant and required; it does not make the evaluator lenient.
- `scripts/ingest_canonical_jsonl.py`: stamps collection metadata/fingerprint after ingest when a source JSONL path is available. Relevant to collection fingerprint/local embedding/profile collection compatibility; keep.
- `README.md`: documents free/local-only chat mode and the validation command. Relevant and required.
- `scripts/run_free_extractive_chat_mode_check.sh`: fixed validation entrypoint requested for this finish. Relevant and required.

## scripts/ingest_canonical_jsonl.py Decision
Decision: keep the change.

Reason:
- The added `stamp_collection_metadata()` call records source JSONL path, chunk count, embedding provider/model, and embedding dimension on a Chroma collection.
- This is tied to local embedding and collection compatibility checks, not an unrelated chat behavior change.
- The change is passive during normal chat validation and does not reset, delete, reingest, or overwrite any collection.

## Safety Confirmation
- OpenAI API key remains optional for extractive mode.
- No vectorstore deletion was performed.
- No collection reset was performed.
- No ingestion/reset command was run.
- No production collection was overwritten.
- No git push or commit was performed.
- No secrets or API keys were displayed.
- Evaluators were not relaxed to pass failures.
- Empty answer is not counted as abstained.

## Validation Evidence
- Command: `bash scripts/run_free_extractive_chat_mode_check.sh`
- Result: passed.
- Summary: `artifacts/free_extractive_chat_mode/validation_summary.json`
