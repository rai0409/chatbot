# Free/Local-only Extractive Chat Mode Report

## Executive Summary
- Status: passed.
- Validation command: `bash scripts/run_free_extractive_chat_mode_check.sh`
- Mode: `CHAT_GENERATION_MODE=extractive`
- OpenAI chat completion: not called in extractive mode.
- OpenAI API key: not required for this validation path.
- Collection: `chatbot_chunks_v1_aligned_candidate`
- Embeddings: local, `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`

## Scope
- Added/finished free local-only extractive `/chat` behavior.
- Preserved approved exact-match short-circuiting.
- Preserved normal retrieval against the existing vectorstore.
- Added a fixed validation script and README setup notes.
- Did not reset, delete, reingest, or overwrite any production collection.

## Validation Results
- Compile: passed.
- `/health`: passed.
- Manual unknown_006 `/chat`: passed.
- Exact QA: `118/118`, `answer_match_rate=1.0`, `approved_exact_rate=1.0`, `llm_fallback_count=0`.
- Unknown abstention: `32/32`, `unsupported_answer_count=0`, `approved_exact_false_positive_count=0`.
- Normal retrieval: `hybrid_hit@5=1.0`, `still_failed=[]`.

## Health Result
- `status`: `ok`
- `keyword_index_loaded`: `true`
- `keyword_index_records`: `116`
- `chroma_collection`: `chatbot_chunks_v1_aligned_candidate`
- `embed_provider`: `local`
- `chat_generation_mode`: `extractive`
- Evidence: `artifacts/free_extractive_chat_mode/health.json`

## Manual Chat Result
- Case: `unknown_006`
- HTTP status: `200`
- `answer_text`: non-empty abstention text
- `used_fallback`: `true`
- `answer_mode`: `fallback`
- `guard_reason`: `insufficient_evidence`
- Unsupported definitive answer: none.
- Evidence: `artifacts/free_extractive_chat_mode/unknown_006_manual_chat_response.json`

## Exact QA Result
- `total_cases`: `118`
- `errors`: `0`
- `answer_match_rate`: `1.0`
- `approved_exact_rate`: `1.0`
- `llm_fallback_count`: `0`
- Evidence: `artifacts/free_extractive_chat_mode/chat_exact_qa/`

## Unknown Abstention Result
- `total_cases`: `32`
- `errors`: `0`
- `abstained_count`: `32`
- `unsupported_answer_count`: `0`
- `approved_exact_false_positive_count`: `0`
- Evidence: `artifacts/free_extractive_chat_mode/unknown_abstention/`

## Normal Retrieval Result
- `total_cases`: `32`
- `vector_hit@5`: `0.9375`
- `hybrid_hit@5`: `1.0`
- `hybrid_mrr`: `0.96875`
- `still_failed`: `[]`
- Evidence: `artifacts/free_extractive_chat_mode/normal_retrieval_candidate/`

## Implementation Notes
- `config.py` adds `CHAT_GENERATION_MODE`, `resolve_chat_generation_mode()`, and shared Chroma collection resolution.
- `rag_core/qa.py` adds conservative extractive evidence checks, grounded extractive answer construction, and abstention fallback.
- `webapi/main.py` skips `ensure_openai_client()` in extractive mode, exposes mode fields, and includes `retrieval_source=approved_qa_exact` on approved exact payloads.
- `tools/evaluate_unknown_abstention.py` now treats non-200 HTTP responses as evaluator errors.
- `scripts/run_free_extractive_chat_mode_check.sh` runs the fixed end-to-end validation without requiring an OpenAI key.

## Commercial Judgment
- Free/local-only extractive mode is suitable for low-cost regression of approved exact answers, conservative grounded extractive answers, unknown abstention, and retrieval quality.
- It is intentionally conservative and should not be treated as a replacement for full LLM answer quality evaluation where synthesis is required.
- The commercial gates requested for this finish pass.

## Commit Plan
Commit 1: `Add free extractive chat mode`

Include:
- `config.py`
- `rag_core/qa.py`
- `rag_core/store.py`
- `webapi/main.py`
- `tools/evaluate_unknown_abstention.py`
- `.env.example`
- `scripts/run_free_extractive_chat_mode_check.sh`
- `README.md`
- `scripts/ingest_canonical_jsonl.py`

Commit 2: `Add free extractive chat mode validation evidence`

Include:
- `artifacts/free_extractive_chat_mode/`

## Remaining Risks
- Extractive mode can abstain where an LLM might synthesize a useful grounded answer.
- The evidence gate is lexical/contextual, not a semantic entailment model.
- Hugging Face local model loading may emit unauthenticated Hub warnings if the model cache is cold, but the validation completed.

## Next Steps
- Keep this script as the recurring free/local-only gate.
- Add a separate grounded-extractive usefulness suite if extractive mode becomes user-facing beyond regression.
