# Normal Retrieval Vector vs Hybrid Evaluation

## Executive Summary
- total_cases: 32
- collection: chatbot_chunks_v1
- keyword_index_loaded: True
- vector_hit@1: 0.000
- vector_hit@5: 0.000
- vector_mrr: 0.000
- hybrid_hit@1: 0.938
- hybrid_hit@5: 1.000
- hybrid_mrr: 0.969

## Hybrid Runtime Signals
- top1_retrieval_source_distribution: `{"keyword": 32}`
- top5_retrieval_source_distribution: `{"keyword": 128, "vector": 32}`
- top1_bm25_score_presence: 32
- top5_bm25_score_presence: 32
- top1_rrf_score_presence: 32
- top5_rrf_score_presence: 32
- top1_keyword_score_presence: 32
- top5_keyword_score_presence: 32

## Improved By Hybrid
- normal_001: vector_rank=0, hybrid_rank=1, hybrid_source=keyword, expected=040219e-biscfaq.pdf, top=040219e-biscfaq.pdf
- normal_002: vector_rank=0, hybrid_rank=1, hybrid_source=keyword, expected=040219e-biscfaq.pdf, top=040219e-biscfaq.pdf
- normal_003: vector_rank=0, hybrid_rank=1, hybrid_source=keyword, expected=040219e-biscfaq.pdf, top=040219e-biscfaq.pdf
- normal_004: vector_rank=0, hybrid_rank=1, hybrid_source=keyword, expected=040219e-biscfaq.pdf, top=040219e-biscfaq.pdf
- normal_005: vector_rank=0, hybrid_rank=1, hybrid_source=keyword, expected=040219e-biscfaq.pdf, top=040219e-biscfaq.pdf
- normal_006: vector_rank=0, hybrid_rank=1, hybrid_source=keyword, expected=040219e-biscfaq.pdf, top=040219e-biscfaq.pdf
- normal_007: vector_rank=0, hybrid_rank=1, hybrid_source=keyword, expected=040219e-biscfaq.pdf, top=040219e-biscfaq.pdf
- normal_008: vector_rank=0, hybrid_rank=1, hybrid_source=keyword, expected=040219e-biscfaq.pdf, top=040219e-biscfaq.pdf
- normal_009: vector_rank=0, hybrid_rank=1, hybrid_source=keyword, expected=040219e-biscfaq.pdf, top=040219e-biscfaq.pdf
- normal_010: vector_rank=0, hybrid_rank=1, hybrid_source=keyword, expected=040219e-biscfaq.pdf, top=040219e-biscfaq.pdf
- normal_011: vector_rank=0, hybrid_rank=2, hybrid_source=keyword, expected=0022009-090.pdf, top=pure_scan_test_144dpi.pdf
- normal_012: vector_rank=0, hybrid_rank=2, hybrid_source=keyword, expected=0022009-090.pdf, top=pure_scan_test_144dpi.pdf
- normal_013: vector_rank=0, hybrid_rank=1, hybrid_source=keyword, expected=0022009-090.pdf, top=0022009-090.pdf
- normal_014: vector_rank=0, hybrid_rank=1, hybrid_source=keyword, expected=0022009-090.pdf, top=0022009-090.pdf
- normal_015: vector_rank=0, hybrid_rank=1, hybrid_source=keyword, expected=0022009-090.pdf, top=0022009-090.pdf
- normal_016: vector_rank=0, hybrid_rank=1, hybrid_source=keyword, expected=0022009-090.pdf, top=0022009-090.pdf
- normal_017: vector_rank=0, hybrid_rank=1, hybrid_source=keyword, expected=0022009-090.pdf, top=0022009-090.pdf
- normal_018: vector_rank=0, hybrid_rank=1, hybrid_source=keyword, expected=0022009-090.pdf, top=0022009-090.pdf
- normal_019: vector_rank=0, hybrid_rank=1, hybrid_source=keyword, expected=0022009-090.pdf, top=0022009-090.pdf
- normal_020: vector_rank=0, hybrid_rank=1, hybrid_source=keyword, expected=0022009-090.pdf, top=0022009-090.pdf
- normal_021: vector_rank=0, hybrid_rank=1, hybrid_source=keyword, expected=r6_tokushu1_1.pdf, top=r6_tokushu1_1.pdf
- normal_022: vector_rank=0, hybrid_rank=1, hybrid_source=keyword, expected=r6_tokushu1_1.pdf, top=r6_tokushu1_1.pdf
- normal_023: vector_rank=0, hybrid_rank=1, hybrid_source=keyword, expected=r6_tokushu1_1.pdf, top=r6_tokushu1_1.pdf
- normal_024: vector_rank=0, hybrid_rank=1, hybrid_source=keyword, expected=r6_tokushu1_1.pdf, top=r6_tokushu1_1.pdf
- normal_025: vector_rank=0, hybrid_rank=1, hybrid_source=keyword, expected=r6_tokushu1_1.pdf, top=r6_tokushu1_1.pdf
- normal_026: vector_rank=0, hybrid_rank=1, hybrid_source=keyword, expected=r6_tokushu1_1.pdf, top=r6_tokushu1_1.pdf
- normal_027: vector_rank=0, hybrid_rank=1, hybrid_source=keyword, expected=r6_tokushu1_1.pdf, top=r6_tokushu1_1.pdf
- normal_028: vector_rank=0, hybrid_rank=1, hybrid_source=keyword, expected=r6_tokushu1_1.pdf, top=r6_tokushu1_1.pdf
- normal_029: vector_rank=0, hybrid_rank=1, hybrid_source=keyword, expected=20241105-benefits-individual-guide-manual.pdf, top=20241105-benefits-individual-guide-manual.pdf
- normal_030: vector_rank=0, hybrid_rank=1, hybrid_source=keyword, expected=20241105-benefits-individual-guide-manual.pdf, top=20241105-benefits-individual-guide-manual.pdf
- normal_031: vector_rank=0, hybrid_rank=1, hybrid_source=keyword, expected=20241105-benefits-individual-guide-manual.pdf, top=20241105-benefits-individual-guide-manual.pdf
- normal_032: vector_rank=0, hybrid_rank=1, hybrid_source=keyword, expected=20241105-benefits-individual-guide-manual.pdf, top=20241105-benefits-individual-guide-manual.pdf

## Regressed By Hybrid
- none

## Still Failed
- none

## Commercial Judgment
- This evaluates normal PDF chunks only and bypasses approved QA exact lookup.
- Hybrid quality is acceptable only if it improves or preserves source_doc/page matching without increasing false positives.
- Use this report as a measurement baseline; do not treat approved QA exact scores as normal retrieval quality.
