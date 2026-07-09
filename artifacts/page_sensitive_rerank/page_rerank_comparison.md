# Page Rerank Comparison

## Metrics

| metric | production | aligned_before | aligned_after |
|---|---:|---:|---:|
| hybrid_hit@1 | 0.9375 | 0.9375 | 0.9375 |
| hybrid_hit@3 | 1.0 | 1.0 | 1.0 |
| hybrid_hit@5 | 1.0 | 1.0 | 1.0 |
| hybrid_mrr | 0.96875 | 0.96875 | 0.96875 |
| hybrid_page_match@5 | 0.96875 | 0.9375 | 0.96875 |
| vector_hit@1 | 0.0 | 0.875 | 0.875 |
| vector_hit@5 | 0.0 | 0.9375 | 0.9375 |
| vector_mrr | 0.0 | 0.9010416666666666 | 0.9010416666666666 |

## Focus Cases

| case | production page rank | aligned_before page rank | aligned_after page rank | after page_match@5 |
|---|---:|---:|---:|---|
| normal_011 | 2 | 0 | 3 | True |
| normal_012 | 2 | 3 | 2 | True |

## Interpretation

The page-sensitive adjustment restores `hybrid_page_match@5` from 0.9375 to 0.96875 while preserving `hybrid_hit@1=0.9375`, `hybrid_hit@5=1.000`, and `hybrid_mrr=0.96875`. normal_011 expected page returns to top5 at rank 3; normal_012 improves from rank 3 to rank 2 for the expected page.

- regressed_by_hybrid after: ['normal_011', 'normal_012']
- still_failed after: []

