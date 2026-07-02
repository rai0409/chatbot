# Normal Retrieval Vector vs Hybrid Evaluation

## Executive Summary
- total_cases: 32
- collection: chatbot_chunks_v1
- keyword_index_loaded: True
- vector_hit@1: 0.000
- vector_hit@5: 0.000
- vector_mrr: 0.000
- hybrid_hit@1: 0.000
- hybrid_hit@5: 1.000
- hybrid_mrr: 0.484

## Hybrid Runtime Signals
- top1_retrieval_source_distribution: `{"vector": 32}`
- top5_retrieval_source_distribution: `{"keyword": 64, "vector": 96}`
- top1_bm25_score_presence: 0
- top5_bm25_score_presence: 32
- top1_rrf_score_presence: 32
- top5_rrf_score_presence: 32
- top1_keyword_score_presence: 0
- top5_keyword_score_presence: 0

## Improved By Hybrid
- normal_001: vector_rank=0, hybrid_rank=2, hybrid_source=vector, expected=040219e-biscfaq.pdf, top=58887_95105_misc.pdf
- normal_002: vector_rank=0, hybrid_rank=2, hybrid_source=vector, expected=040219e-biscfaq.pdf, top=58887_95105_misc.pdf
- normal_003: vector_rank=0, hybrid_rank=2, hybrid_source=vector, expected=040219e-biscfaq.pdf, top=58887_95105_misc.pdf
- normal_004: vector_rank=0, hybrid_rank=2, hybrid_source=vector, expected=040219e-biscfaq.pdf, top=58887_95105_misc.pdf
- normal_005: vector_rank=0, hybrid_rank=2, hybrid_source=vector, expected=040219e-biscfaq.pdf, top=58887_95105_misc.pdf
- normal_006: vector_rank=0, hybrid_rank=2, hybrid_source=vector, expected=040219e-biscfaq.pdf, top=58887_95105_misc.pdf
- normal_007: vector_rank=0, hybrid_rank=2, hybrid_source=vector, expected=040219e-biscfaq.pdf, top=58887_95105_misc.pdf
- normal_008: vector_rank=0, hybrid_rank=2, hybrid_source=vector, expected=040219e-biscfaq.pdf, top=58887_95105_misc.pdf
- normal_009: vector_rank=0, hybrid_rank=2, hybrid_source=vector, expected=040219e-biscfaq.pdf, top=58887_95105_misc.pdf
- normal_010: vector_rank=0, hybrid_rank=2, hybrid_source=vector, expected=040219e-biscfaq.pdf, top=58887_95105_misc.pdf
- normal_011: vector_rank=0, hybrid_rank=4, hybrid_source=vector, expected=0022009-090.pdf, top=58887_95105_misc.pdf
- normal_012: vector_rank=0, hybrid_rank=4, hybrid_source=vector, expected=0022009-090.pdf, top=58887_95105_misc.pdf
- normal_013: vector_rank=0, hybrid_rank=2, hybrid_source=vector, expected=0022009-090.pdf, top=58887_95105_misc.pdf
- normal_014: vector_rank=0, hybrid_rank=2, hybrid_source=vector, expected=0022009-090.pdf, top=58887_95105_misc.pdf
- normal_015: vector_rank=0, hybrid_rank=2, hybrid_source=vector, expected=0022009-090.pdf, top=58887_95105_misc.pdf
- normal_016: vector_rank=0, hybrid_rank=2, hybrid_source=vector, expected=0022009-090.pdf, top=58887_95105_misc.pdf
- normal_017: vector_rank=0, hybrid_rank=2, hybrid_source=vector, expected=0022009-090.pdf, top=58887_95105_misc.pdf
- normal_018: vector_rank=0, hybrid_rank=2, hybrid_source=vector, expected=0022009-090.pdf, top=58887_95105_misc.pdf
- normal_019: vector_rank=0, hybrid_rank=2, hybrid_source=vector, expected=0022009-090.pdf, top=58887_95105_misc.pdf
- normal_020: vector_rank=0, hybrid_rank=2, hybrid_source=vector, expected=0022009-090.pdf, top=58887_95105_misc.pdf
- normal_021: vector_rank=0, hybrid_rank=2, hybrid_source=vector, expected=r6_tokushu1_1.pdf, top=58887_95105_misc.pdf
- normal_022: vector_rank=0, hybrid_rank=2, hybrid_source=vector, expected=r6_tokushu1_1.pdf, top=58887_95105_misc.pdf
- normal_023: vector_rank=0, hybrid_rank=2, hybrid_source=vector, expected=r6_tokushu1_1.pdf, top=58887_95105_misc.pdf
- normal_024: vector_rank=0, hybrid_rank=2, hybrid_source=vector, expected=r6_tokushu1_1.pdf, top=58887_95105_misc.pdf
- normal_025: vector_rank=0, hybrid_rank=2, hybrid_source=vector, expected=r6_tokushu1_1.pdf, top=58887_95105_misc.pdf
- normal_026: vector_rank=0, hybrid_rank=2, hybrid_source=vector, expected=r6_tokushu1_1.pdf, top=58887_95105_misc.pdf
- normal_027: vector_rank=0, hybrid_rank=2, hybrid_source=vector, expected=r6_tokushu1_1.pdf, top=58887_95105_misc.pdf
- normal_028: vector_rank=0, hybrid_rank=2, hybrid_source=vector, expected=r6_tokushu1_1.pdf, top=58887_95105_misc.pdf
- normal_029: vector_rank=0, hybrid_rank=2, hybrid_source=vector, expected=20241105-benefits-individual-guide-manual.pdf, top=58887_95105_misc.pdf
- normal_030: vector_rank=0, hybrid_rank=2, hybrid_source=vector, expected=20241105-benefits-individual-guide-manual.pdf, top=58887_95105_misc.pdf
- normal_031: vector_rank=0, hybrid_rank=2, hybrid_source=vector, expected=20241105-benefits-individual-guide-manual.pdf, top=58887_95105_misc.pdf
- normal_032: vector_rank=0, hybrid_rank=2, hybrid_source=vector, expected=20241105-benefits-individual-guide-manual.pdf, top=58887_95105_misc.pdf

## Regressed By Hybrid
- none

## Still Failed
- none

## Commercial Judgment
- This evaluates normal PDF chunks only and bypasses approved QA exact lookup.
- Hybrid quality is acceptable only if it improves or preserves source_doc/page matching without increasing false positives.
- Use this report as a measurement baseline; do not treat approved QA exact scores as normal retrieval quality.
