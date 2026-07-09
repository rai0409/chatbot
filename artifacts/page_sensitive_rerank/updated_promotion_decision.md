# Updated Promotion Decision

## Decision: promote_ready

## Evidence

- /health: status=ok, keyword_index_loaded=True, keyword_index_records=116
- exact QA: 118/118
- unknown abstention: 32/32
- aligned_test hybrid_hit@1: 0.9375
- aligned_test hybrid_hit@5: 1.0
- aligned_test hybrid_mrr: 0.96875
- aligned_test hybrid_page_match@5: 0.96875
- normal_011 expected page rank: 3

## Guardrails

Production collection was not overwritten. The change is code-level hybrid scoring and was validated against `chatbot_chunks_v1_aligned_test`.

## Commercial Judgment

Aligned collection is now promotion-ready for the current PDF corpus under the tested QA, unknown-abstention, and 32-case normal retrieval suite. Promotion should still follow the existing backup, fingerprint stamp, config switch, live regression, and rollback plan rather than direct overwrite.
