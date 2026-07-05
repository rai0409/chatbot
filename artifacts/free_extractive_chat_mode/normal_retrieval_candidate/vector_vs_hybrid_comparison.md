# Normal Retrieval Vector vs Hybrid Evaluation

## Executive Summary
- total_cases: 32
- collection: chatbot_chunks_v1_aligned_candidate
- keyword_index_loaded: True
- vector_hit@1: 0.875
- vector_hit@5: 0.938
- vector_mrr: 0.901
- hybrid_hit@1: 0.938
- hybrid_hit@5: 1.000
- hybrid_mrr: 0.969

## Hybrid Runtime Signals
- top1_retrieval_source_distribution: `{"hybrid": 31, "keyword": 1}`
- top5_retrieval_source_distribution: `{"hybrid": 152, "keyword": 8}`
- top1_bm25_score_presence: 32
- top5_bm25_score_presence: 32
- top1_rrf_score_presence: 32
- top5_rrf_score_presence: 32
- top1_keyword_score_presence: 32
- top5_keyword_score_presence: 32

## Improved By Hybrid
- normal_001: vector_rank=2, hybrid_rank=1, hybrid_source=keyword, expected=040219e-biscfaq.pdf, top=040219e-biscfaq.pdf
- normal_002: vector_rank=0, hybrid_rank=1, hybrid_source=hybrid, expected=040219e-biscfaq.pdf, top=040219e-biscfaq.pdf
- normal_005: vector_rank=0, hybrid_rank=1, hybrid_source=hybrid, expected=040219e-biscfaq.pdf, top=040219e-biscfaq.pdf
- normal_027: vector_rank=3, hybrid_rank=1, hybrid_source=hybrid, expected=r6_tokushu1_1.pdf, top=r6_tokushu1_1.pdf

## Regressed By Hybrid
- normal_011: vector_rank=1, hybrid_rank=2, hybrid_source=hybrid, expected=0022009-090.pdf, top=pure_scan_test_144dpi.pdf
- normal_012: vector_rank=1, hybrid_rank=2, hybrid_source=hybrid, expected=0022009-090.pdf, top=pure_scan_test_144dpi.pdf

## Still Failed
- none

## Commercial Judgment
- This evaluates normal PDF chunks only and bypasses approved QA exact lookup.
- Hybrid quality is acceptable only if it improves or preserves source_doc/page matching without increasing false positives.
- Use this report as a measurement baseline; do not treat approved QA exact scores as normal retrieval quality.
