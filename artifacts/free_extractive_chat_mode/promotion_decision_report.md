# Promotion Decision Report

## Executive Summary
- Candidate collection `chatbot_chunks_v1_aligned_candidate` is green on the free/local extractive gate.
- Production promote has not been executed.
- Production overwrite remains forbidden.
- Decision: conditionally approved for controlled production candidate promotion.
- `promote_candidate`: yes, conditional on a controlled environment-variable switch with rollback evidence.
- The approved scope is limited to the verified PDF-derived 116-chunk corpus and `CHAT_GENERATION_MODE=extractive`.

## Candidate Collection
- Collection name: `chatbot_chunks_v1_aligned_candidate`
- Chunk count: `116`
- Keyword index records: `116`
- Canonical normalized JSONL path: `index/chunks.canonical.normalized.jsonl`
- Embedding provider: `local`
- Embedding model: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- Embedding dimension: `384`
- Chat generation mode: `extractive`
- Source JSONL SHA-256: `b996788757c68951908842f22172ba5939826fa37ac8b0d37a5220608efabc5e`
- Fingerprint builder version: `chroma_fingerprint_v1`

Health summary:
- `status=ok`
- `chroma_collection=chatbot_chunks_v1_aligned_candidate`
- `vectorstore_collection_name=chatbot_chunks_v1_aligned_candidate`
- `keyword_index_loaded=true`
- `keyword_index_records=116`
- `embed_provider=local`
- `chat_generation_mode=extractive`

Alignment summary:
- BM25 total chunks: `116`
- Chroma total chunks: `116`
- Matching chunk IDs: `116`
- Chunk ID Jaccard: `1.0`
- BM25-only chunks: `0`
- Chroma-only chunks: `0`
- Source document diffs: none
- Required retrieval metadata missing counts: none for BM25 and Chroma

## Validation Evidence
- Exact QA gate: `118/118`, `errors=0`, `answer_match_rate=1.0`, `approved_exact_rate=1.0`, `llm_fallback_count=0`
- Unknown abstention gate: `32/32`, `errors=0`, `abstained_count=32`, `unsupported_answer_count=0`, `approved_exact_false_positive_count=0`
- Normal retrieval gate: `hybrid_hit@5=1.0`, `still_failed=[]`
- `/chat` manual smoke: `unknown_006` returned HTTP `200`, non-empty safe fallback, `used_fallback=true`
- Compileall: `python -m compileall config.py rag_core webapi tools` passed as part of `scripts/run_free_extractive_chat_mode_check.sh`
- Canonical metadata audit support: `rag_core/canonical_metadata.py`
- Fingerprint audit support: `rag_core/embedding_fingerprint.py`
- Corpus alignment audit support: `tools/audit_corpus_alignment.py`
- Normalized canonical chunk builder: `tools/build_normalized_canonical_chunks.py`
- Fingerprint stamping tool: `tools/stamp_chroma_collection_fingerprint.py`

Evidence artifact paths:
- `artifacts/free_extractive_chat_mode/validation_summary.json`
- `artifacts/free_extractive_chat_mode/health.json`
- `artifacts/free_extractive_chat_mode/unknown_006_manual_chat_response.json`
- `artifacts/free_extractive_chat_mode/chat_exact_qa/`
- `artifacts/free_extractive_chat_mode/unknown_abstention/`
- `artifacts/free_extractive_chat_mode/normal_retrieval_candidate/`
- `artifacts/free_extractive_chat_mode/candidate_alignment_audit/corpus_alignment_summary.json`
- `artifacts/free_extractive_chat_mode/candidate_alignment_audit/corpus_alignment_report.md`

## Promotion Decision
Decision: conditionally approved for controlled production candidate promotion.

Conditions:
- No production collection overwrite.
- No vectorstore deletion.
- No collection reset.
- No ingestion reset.
- Promotion may only be performed by switching runtime configuration to the candidate collection.
- Rollback must be possible by restoring the previous collection name in runtime configuration.
- Scope is limited to the verified PDF-derived 116-chunk corpus.
- Scope is limited to free/local-only extractive mode.
- DOCX/CSV/XLSX/PPTX behavior is outside this decision.
- LLM mode answer quality is outside this decision.

This report does not execute promotion. It only records that the candidate is eligible for a controlled switch plan.

## Allowed Promotion Method
Allowed method:
- Change the runtime `CHROMA_COLLECTION` environment variable to `chatbot_chunks_v1_aligned_candidate`.
- Keep the existing production collection intact.
- Keep the previous production collection name recorded before the switch.
- Keep the old collection available for immediate rollback.
- Restart or reload the runtime using the changed environment.
- Capture post-switch `/health`.
- Run post-switch smoke tests.

Rollback must be a configuration-only revert:
- Set `CHROMA_COLLECTION` back to the previous collection name.
- Restart or reload the runtime.
- Confirm `/health` reports the previous collection.
- Run the rollback smoke checks.

## Forbidden Actions
- Production collection overwrite.
- Vectorstore delete.
- Collection reset.
- Ingestion reset.
- Silent evaluator relaxation.
- Empty-answer abstention counting.
- Unsupported file type support claim.
- LLM quality support claim.
- Secret/API key exposure.
- `.env` secret value disclosure.

## Rollback Plan
Rollback triggers:
- `/health` does not return `status=ok`.
- `/health` reports the wrong collection, provider, or mode.
- Approved exact sample fails.
- Unknown sample returns unsupported definitive answer.
- Normal retrieval sample regresses below gate.
- Error rate or latency is unacceptable for production traffic.
- Logs show repeated retrieval, vectorstore, or API errors.

Rollback command outline:

```bash
export CHROMA_COLLECTION=<previous_collection_name>
```

Then restart or reload the service using the normal deployment mechanism.

Health check after rollback:
- `/health` returns `status=ok`.
- `/health` reports the previous collection.
- Keyword index remains loaded.

Validation check after rollback:
- Run one approved exact sample.
- Run one unknown abstention sample.
- Run one normal retrieval sample.
- Check logs for startup and request errors.

## Production Smoke Test Plan
Run after a controlled switch, before broad traffic exposure:
- `/health`
  - Verify status, collection, provider, keyword index, and chat mode.
- Approved exact sample
  - Verify `answer_mode=approved_exact_match`.
  - Verify approved answer text and citation metadata.
- Unknown sample
  - Use `unknown_006`.
  - Verify HTTP `200`, non-empty safe fallback, no unsupported definitive answer.
- Normal retrieval sample
  - Verify expected source document appears in top retrieved evidence.
- `/chat` manual smoke
  - Verify answer payload contains `answer_text`, `answer_mode`, `chat_generation_mode`, and citations or explicit fallback.
- Log check
  - Confirm no repeated vectorstore, retrieval, auth, or generation errors.
- No API key mode check
  - Confirm extractive mode does not require OpenAI chat completion.

## Commercial Readiness Assessment
| Area | Rating | Assessment |
|---|---|---|
| safety | Green | Unknown abstention is green, unsupported answers are zero in the 32-case gate, and fallback is explicit. |
| reproducibility | Green | Fixed script and artifacts reproduce exact QA, unknown abstention, normal retrieval, health, and manual smoke evidence. |
| observability | Green | `/health`, validation artifacts, alignment audit, fingerprint metadata, and per-case outputs are available. |
| cost control | Green | Extractive mode does not call OpenAI chat completion and local embeddings are used for this gate. |
| rollbackability | Green | Allowed promotion is environment-variable based and preserves the old collection for config-only rollback. |
| supported scope clarity | Green | The verified scope is explicit: PDF-derived 116 chunks, local embeddings, extractive mode. |
| unsupported scope clarity | Green | Unsupported scope is explicitly documented: production promotion not executed, LLM mode not validated, DOCX/CSV/XLSX/PPTX not claimed. |
| operational documentation | Yellow | Gate and decision documents exist, but a deployment-specific controlled switch plan is still needed. |
| user-facing answer quality | Yellow | Safety gates are green, but long-form grounded extractive usefulness has not been separately evaluated. |
| ingestion flexibility | Red | Commercial upload/E2E ingestion for browser upload and non-PDF formats is not verified in this decision. |

## Not Yet Commercially Complete
- Browser upload is not implemented/verified as a commercial E2E workflow here.
- DOCX/CSV/XLSX/PPTX are not verified for this promotion decision.
- LLM mode answer quality is not evaluated.
- Long-form extractive answer quality is not evaluated.
- Multi-tenant / auth / audit log sufficiency for commercial requirements is not confirmed.
- Production monitoring is not fixed.
- Production promotion decision execution has not occurred.

## Recommended Next Step
- Do not overwrite production.
- Create a controlled production switch plan that changes only `CHROMA_COLLECTION`.
- Record previous collection name, deployment restart steps, post-switch smoke tests, and rollback steps.
- After that, run a grounded extractive answer quality evaluation.
- After that, design and verify PDF upload E2E.
