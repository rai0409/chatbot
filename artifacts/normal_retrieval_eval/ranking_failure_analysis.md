# Hybrid Ranking Failure Analysis

## Summary
- total_cases: 32
- before_hit@1: 0.000
- before_hit@3: 0.938
- before_hit@5: 1.000
- before_mrr: 0.484
- top1_retrieval_source_distribution: `{"vector": 32}`
- correct_candidate_retrieval_source_distribution: `{"keyword": 32}`
- correct_candidate_rank_distribution: `{"2": 30, "4": 2}`
- same_rrf_top1_and_correct_candidate: 30/32
- correct_has_bm25_top1_does_not: 32/32
- correct_keyword_score_gt_top1: 31/32

## Diagnosis
- RRF gives vector rank1 and keyword rank1 the same score; vector buckets are inserted first and win stable sorting.
- The correct candidates are already in top5, usually as keyword hits with BM25 and higher keyword evidence.
- The failure is ranking/fusion, not candidate generation.

## Cases
- normal_001: correct_rank=2, top1=58887_95105_misc.pdf(vector, rrf=0.01639344262295082, bm25=, keyword=0.03), correct=040219e-biscfaq.pdf(keyword, rrf=0.01639344262295082, bm25=58.33165069334518, keyword=1.0)
- normal_002: correct_rank=2, top1=58887_95105_misc.pdf(vector, rrf=0.01639344262295082, bm25=, keyword=0.03), correct=040219e-biscfaq.pdf(keyword, rrf=0.01639344262295082, bm25=34.60755380625525, keyword=0.9)
- normal_003: correct_rank=2, top1=58887_95105_misc.pdf(vector, rrf=0.01639344262295082, bm25=, keyword=0.51), correct=040219e-biscfaq.pdf(keyword, rrf=0.01639344262295082, bm25=21.69486262558792, keyword=1.0)
- normal_004: correct_rank=2, top1=58887_95105_misc.pdf(vector, rrf=0.01639344262295082, bm25=, keyword=0.03), correct=040219e-biscfaq.pdf(keyword, rrf=0.01639344262295082, bm25=18.378234756819072, keyword=0.52)
- normal_005: correct_rank=2, top1=58887_95105_misc.pdf(vector, rrf=0.01639344262295082, bm25=, keyword=0.03), correct=040219e-biscfaq.pdf(keyword, rrf=0.01639344262295082, bm25=35.063199621935766, keyword=0.9)
- normal_006: correct_rank=2, top1=58887_95105_misc.pdf(vector, rrf=0.01639344262295082, bm25=, keyword=0.03), correct=040219e-biscfaq.pdf(keyword, rrf=0.01639344262295082, bm25=74.54626938394888, keyword=1.0)
- normal_007: correct_rank=2, top1=58887_95105_misc.pdf(vector, rrf=0.01639344262295082, bm25=, keyword=0.03), correct=040219e-biscfaq.pdf(keyword, rrf=0.01639344262295082, bm25=29.475576648575977, keyword=0.64)
- normal_008: correct_rank=2, top1=58887_95105_misc.pdf(vector, rrf=0.01639344262295082, bm25=, keyword=0.03), correct=040219e-biscfaq.pdf(keyword, rrf=0.01639344262295082, bm25=22.325164046210336, keyword=0.52)
- normal_009: correct_rank=2, top1=58887_95105_misc.pdf(vector, rrf=0.01639344262295082, bm25=, keyword=0.51), correct=040219e-biscfaq.pdf(keyword, rrf=0.01639344262295082, bm25=22.055682107126632, keyword=0.64)
- normal_010: correct_rank=2, top1=58887_95105_misc.pdf(vector, rrf=0.01639344262295082, bm25=, keyword=0.03), correct=040219e-biscfaq.pdf(keyword, rrf=0.01639344262295082, bm25=24.37985853233156, keyword=0.64)
- normal_011: correct_rank=4, top1=58887_95105_misc.pdf(vector, rrf=0.01639344262295082, bm25=, keyword=0.11), correct=0022009-090.pdf(keyword, rrf=0.016129032258064516, bm25=23.154819522477794, keyword=0.66)
- normal_012: correct_rank=4, top1=58887_95105_misc.pdf(vector, rrf=0.01639344262295082, bm25=, keyword=0.03), correct=0022009-090.pdf(keyword, rrf=0.016129032258064516, bm25=6.160113334352174, keyword=0.52)
- normal_013: correct_rank=2, top1=58887_95105_misc.pdf(vector, rrf=0.01639344262295082, bm25=, keyword=0.03), correct=0022009-090.pdf(keyword, rrf=0.01639344262295082, bm25=27.162462866136163, keyword=0.26)
- normal_014: correct_rank=2, top1=58887_95105_misc.pdf(vector, rrf=0.01639344262295082, bm25=, keyword=0.03), correct=0022009-090.pdf(keyword, rrf=0.01639344262295082, bm25=35.55375261210906, keyword=1.0)
- normal_015: correct_rank=2, top1=58887_95105_misc.pdf(vector, rrf=0.01639344262295082, bm25=, keyword=0.51), correct=0022009-090.pdf(keyword, rrf=0.01639344262295082, bm25=23.671872612718737, keyword=1.0)
- normal_016: correct_rank=2, top1=58887_95105_misc.pdf(vector, rrf=0.01639344262295082, bm25=, keyword=0.03), correct=0022009-090.pdf(keyword, rrf=0.01639344262295082, bm25=36.18177058802578, keyword=1.0)
- normal_017: correct_rank=2, top1=58887_95105_misc.pdf(vector, rrf=0.01639344262295082, bm25=, keyword=0.11), correct=0022009-090.pdf(keyword, rrf=0.01639344262295082, bm25=17.116254899664217, keyword=0.64)
- normal_018: correct_rank=2, top1=58887_95105_misc.pdf(vector, rrf=0.01639344262295082, bm25=, keyword=0.03), correct=0022009-090.pdf(keyword, rrf=0.01639344262295082, bm25=15.612309942048187, keyword=0.52)
- normal_019: correct_rank=2, top1=58887_95105_misc.pdf(vector, rrf=0.01639344262295082, bm25=, keyword=0.03), correct=0022009-090.pdf(keyword, rrf=0.01639344262295082, bm25=40.252090243783535, keyword=1.0)
- normal_020: correct_rank=2, top1=58887_95105_misc.pdf(vector, rrf=0.01639344262295082, bm25=, keyword=0.03), correct=0022009-090.pdf(keyword, rrf=0.01639344262295082, bm25=36.36072939803262, keyword=1.0)
- normal_021: correct_rank=2, top1=58887_95105_misc.pdf(vector, rrf=0.01639344262295082, bm25=, keyword=0.29), correct=r6_tokushu1_1.pdf(keyword, rrf=0.01639344262295082, bm25=13.216979987822162, keyword=0.26)
- normal_022: correct_rank=2, top1=58887_95105_misc.pdf(vector, rrf=0.01639344262295082, bm25=, keyword=0.03), correct=r6_tokushu1_1.pdf(keyword, rrf=0.01639344262295082, bm25=17.85121689750581, keyword=0.26)
- normal_023: correct_rank=2, top1=58887_95105_misc.pdf(vector, rrf=0.01639344262295082, bm25=, keyword=0.03), correct=r6_tokushu1_1.pdf(keyword, rrf=0.01639344262295082, bm25=23.453593446238393, keyword=0.52)
- normal_024: correct_rank=2, top1=58887_95105_misc.pdf(vector, rrf=0.01639344262295082, bm25=, keyword=0.03), correct=r6_tokushu1_1.pdf(keyword, rrf=0.01639344262295082, bm25=15.71152254931677, keyword=0.97)
- normal_025: correct_rank=2, top1=58887_95105_misc.pdf(vector, rrf=0.01639344262295082, bm25=, keyword=0.03), correct=r6_tokushu1_1.pdf(keyword, rrf=0.01639344262295082, bm25=20.301853121903758, keyword=0.26)
- normal_026: correct_rank=2, top1=58887_95105_misc.pdf(vector, rrf=0.01639344262295082, bm25=, keyword=0.03), correct=r6_tokushu1_1.pdf(keyword, rrf=0.01639344262295082, bm25=22.85214375534802, keyword=0.26)
- normal_027: correct_rank=2, top1=58887_95105_misc.pdf(vector, rrf=0.01639344262295082, bm25=, keyword=0.03), correct=r6_tokushu1_1.pdf(keyword, rrf=0.01639344262295082, bm25=12.784683983517281, keyword=0.26)
- normal_028: correct_rank=2, top1=58887_95105_misc.pdf(vector, rrf=0.01639344262295082, bm25=, keyword=0.03), correct=r6_tokushu1_1.pdf(keyword, rrf=0.01639344262295082, bm25=64.5710340442873, keyword=1.0)
- normal_029: correct_rank=2, top1=58887_95105_misc.pdf(vector, rrf=0.01639344262295082, bm25=, keyword=0.03), correct=20241105-benefits-individual-guide-manual.pdf(keyword, rrf=0.01639344262295082, bm25=66.45245293421223, keyword=0.78)
- normal_030: correct_rank=2, top1=58887_95105_misc.pdf(vector, rrf=0.01639344262295082, bm25=, keyword=0.03), correct=20241105-benefits-individual-guide-manual.pdf(keyword, rrf=0.01639344262295082, bm25=44.54504656168408, keyword=1.0)
- normal_031: correct_rank=2, top1=58887_95105_misc.pdf(vector, rrf=0.01639344262295082, bm25=, keyword=0.03), correct=20241105-benefits-individual-guide-manual.pdf(keyword, rrf=0.01639344262295082, bm25=70.90333146721925, keyword=1.0)
- normal_032: correct_rank=2, top1=58887_95105_misc.pdf(vector, rrf=0.01639344262295082, bm25=, keyword=0.03), correct=20241105-benefits-individual-guide-manual.pdf(keyword, rrf=0.01639344262295082, bm25=22.703595148179247, keyword=0.78)
