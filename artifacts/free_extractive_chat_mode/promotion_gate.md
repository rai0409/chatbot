# Candidate Collection Promotion Gate

## 目的
Fix the gate for evaluating whether a candidate Chroma collection is eligible for production promotion without deleting, resetting, reingesting, or overwriting production data.

This document is a promotion gate, not a promotion record. The candidate has been evaluated for free/local-only extractive mode, but production promotion has not been performed.

## 対象 Candidate Collection
- Candidate collection: `chatbot_chunks_v1_aligned_candidate`
- Expected embedding provider: `local`
- Expected embedding model: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- Expected chat generation mode for this gate: `extractive`

## Active Collection 確認
Required `/health` fields:
- `status=ok`
- `chroma_collection=chatbot_chunks_v1_aligned_candidate`
- `vectorstore_collection_name=chatbot_chunks_v1_aligned_candidate`
- `embed_provider=local`
- `chat_generation_mode=extractive`

Evidence:
- `artifacts/free_extractive_chat_mode/health.json`

## Collection Fingerprint 確認
Required checks before any promotion decision:
- fingerprint is present on the candidate collection.
- active fingerprint matches collection fingerprint.
- source JSONL path is recorded.
- source JSONL SHA-256 is recorded.
- chunk count is recorded.
- embedding provider is recorded.
- embedding model is recorded.
- embedding dimension is recorded.
- builder version is recorded.

Expected fields:
- `source_jsonl_path`
- `source_jsonl_sha256`
- `chunk_count`
- `embed_provider`
- `embed_model`
- `embedding_dim`
- `created_at`
- `builder_version`

If fingerprint metadata is missing, produce an audit report and stop. Do not reset, reingest, or overwrite production collection inside this gate.

## Source JSONL / Chunk Count / Embedding 確認
Required:
- Source JSONL used by BM25 keyword index is identified.
- Candidate collection source JSONL fingerprint matches the intended source.
- Candidate collection count matches the expected source chunk count.
- Embedding provider/model match the local embedding configuration.
- Embedding dimension matches the candidate collection vectors.

Failure handling:
- Treat mismatch as promotion-blocking.
- Do not patch the production collection to force a pass.

## BM25 Keyword Index と Chroma Collection Alignment
Required:
- BM25 JSONL chunk IDs and Chroma collection IDs are aligned.
- Source document distributions are aligned.
- Required retrieval metadata is present for both BM25 and Chroma records.
- Candidate collection fingerprint matches active embedding configuration.

Recommended evidence:
- Corpus alignment summary JSON.
- Corpus alignment Markdown report.
- No unexplained `bm25_only_chunks`.
- No unexplained `vectorstore_only_chunks`.
- Acceptable `chunk_id_jaccard` only if all differences are documented and intentionally excluded.

## Exact QA Gate
Command:

```bash
bash scripts/run_free_extractive_chat_mode_check.sh
```

Required:
- `total_cases=118`
- `errors=0`
- `answer_match_rate=1.0`
- `approved_exact_rate=1.0`
- `llm_fallback_count=0`

Evidence:
- `artifacts/free_extractive_chat_mode/chat_exact_qa/`

## Unknown Abstention Gate
Required:
- `total_cases=32`
- `errors=0`
- `abstained_count=32`
- `unsupported_answer_count=0`
- `approved_exact_false_positive_count=0`
- Empty answers must not count as abstained.

Evidence:
- `artifacts/free_extractive_chat_mode/unknown_abstention/`

## Normal Retrieval Gate
Required:
- `hybrid_hit@5=1.0`
- `still_failed=[]`
- Normal retrieval remains separate from approved exact QA.

Evidence:
- `artifacts/free_extractive_chat_mode/normal_retrieval_candidate/`

## /chat Manual Smoke
Required manual case:
- Case: `unknown_006`
- Question: `大阪府の電子入札システムでJava Plug-in警告が出る原因は何ですか。`

Required result:
- HTTP `200`
- `answer_text` is non-empty.
- `used_fallback=true`
- No unsupported definitive answer.

Evidence:
- `artifacts/free_extractive_chat_mode/unknown_006_manual_chat_response.json`

## /health 確認
Required:
- `/health` is reachable.
- `status=ok`
- keyword index is loaded.
- collection, provider, and chat mode match the gate.

Evidence:
- `artifacts/free_extractive_chat_mode/health.json`

## Rollback 方針
Rollback must be configuration-only:
- Point runtime config back to the previous production collection.
- Keep vectorstore files intact.
- Keep candidate collection intact for postmortem.
- Keep promotion evidence and failure report.

Do not:
- delete vectorstore files
- reset Chroma collections
- overwrite production collection
- mutate production data to hide a failed gate

## Production Overwrite 禁止
This gate does not permit production overwrite.

Promotion, if later approved by a human operator, should be a separate controlled operation with:
- explicit collection name change or runtime config change
- archived pre-promotion `/health`
- archived post-promotion `/health`
- rollback config recorded
- promotion decision report committed or attached

## Promotion Decision Report 作成条件
Create a promotion decision report only when all of the following exist:
- `/health` evidence
- collection fingerprint evidence
- BM25/Chroma alignment evidence
- exact QA gate evidence
- unknown abstention gate evidence
- normal retrieval gate evidence
- manual `/chat` smoke evidence
- explicit rollback config
- explicit statement that no production overwrite/reset/delete was performed

The report must say one of:
- `promote_candidate: yes`
- `promote_candidate: no`

It must not infer promotion from passing tests alone.

## 合格基準
The candidate is eligible for promotion consideration only if:
- health checks pass.
- fingerprint is present and matches active embedding configuration.
- BM25 and Chroma alignment is explained and acceptable.
- exact QA gate passes `118/118`.
- unknown abstention gate passes `32/32`.
- normal retrieval gate has `hybrid_hit@5=1.0` and `still_failed=[]`.
- manual `/chat` smoke returns non-empty safe fallback.
- no evaluator was weakened.
- no production data was overwritten.

## 不合格時の扱い
If any required check fails:
- mark the candidate as not promotable.
- keep all failure artifacts.
- do not run reset or ingestion as part of the gate.
- do not overwrite production collection.
- create a failure report with the failed gate, expected value, actual value, and suspected cause.
- fix code/data in a separate change and rerun the full gate.
