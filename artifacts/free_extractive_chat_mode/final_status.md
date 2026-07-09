# Final Status

## Execution
- Execution datetime: `2026-07-05T23:09:08+09:00`
- Validation command: `bash scripts/run_free_extractive_chat_mode_check.sh`
- Validation status: passed.
- `git commit`: not executed.
- `git push`: not executed.

## Runtime
- Active collection: `chatbot_chunks_v1_aligned_candidate`
- Embed provider: `local`
- Embed model: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- Chat generation mode: `extractive`

## /health Summary
- `status`: `ok`
- `keyword_index_loaded`: `true`
- `keyword_index_records`: `116`
- `chroma_collection`: `chatbot_chunks_v1_aligned_candidate`
- `vectorstore_collection_name`: `chatbot_chunks_v1_aligned_candidate`
- `embed_provider`: `local`
- `chat_generation_mode`: `extractive`

Evidence:
- `artifacts/free_extractive_chat_mode/health.json`

## Exact QA Result
- `total_cases`: `118`
- `errors`: `0`
- `answer_match_rate`: `1.0`
- `approved_exact_rate`: `1.0`
- `llm_fallback_count`: `0`

Evidence:
- `artifacts/free_extractive_chat_mode/chat_exact_qa/`

## Unknown Abstention Result
- `total_cases`: `32`
- `errors`: `0`
- `abstained_count`: `32`
- `unsupported_answer_count`: `0`
- `approved_exact_false_positive_count`: `0`

Evidence:
- `artifacts/free_extractive_chat_mode/unknown_abstention/`

## Normal Retrieval Result
- `hybrid_hit@5`: `1.0`
- `still_failed`: `[]`

Evidence:
- `artifacts/free_extractive_chat_mode/normal_retrieval_candidate/`

## Compileall Result
- `python -m compileall config.py rag_core webapi tools`: passed as part of `scripts/run_free_extractive_chat_mode_check.sh`.

## Commit Preparation
- `artifacts/free_extractive_chat_mode/commit_plan.md` created.
- Commit 1 and Commit 2 candidates are documented.
- Commit has not been run.
- Push has not been run.

## Promotion Gate
- `artifacts/free_extractive_chat_mode/promotion_gate.md` created.
- Candidate collection evaluation gate is documented.
- Production promotion has not been performed.
- Production overwrite/reset/delete was not performed.

## 未対応事項
- Production promotion decision report is not created.
- Production collection has not been changed.
- LLM mode answer quality is not validated here.
- DOCX/CSV/XLSX/PPTX production support is not claimed here.
- Collection fingerprint/alignment checks are specified in the promotion gate; a promotion decision report still needs explicit evidence before any production promotion.

## 次にやるべきこと
- Review commit candidates in `artifacts/free_extractive_chat_mode/commit_plan.md`.
- Decide whether to include the untracked fingerprint/canonical metadata support files in the code commit.
- If promotion is desired later, run the promotion gate and create a separate promotion decision report.
