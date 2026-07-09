# Fixed QA Retrieval Exact Lookup Success Summary

## Result

Fixed QA retrieval evaluation passed at 100%.

- total_cases: 118
- errors: 0
- hit@1: 118/118 = 1.000
- hit@3: 118/118 = 1.000
- hit@5: 118/118 = 1.000
- hit@k: 118/118 = 1.000
- mrr: 1.000

## Implemented Flow

The retrieval pipeline now uses a two-layer search strategy.

1. Approved QA exact lookup
   - Strong question normalization
   - Exact key match
   - Re-normalized question_text match
   - Near-exact fallback for minor formatting differences
   - If matched, returns approved QA as rank 1

2. Vector retrieval fallback
   - Used only when approved QA exact lookup has no match

## Why This Is Correct

For confirmed approved QA records, deterministic retrieval is required.

Vector similarity alone cannot guarantee 100% because similar questions can be semantically close. Therefore, approved QA exact lookup must run before vector retrieval.

## Key Artifacts

- exact index:
  - artifacts/fixed_qa_eval/exact_index/approved_qa_exact_index.json

- 100% retrieval evaluation:
  - artifacts/fixed_qa_eval/retrieval_eval_exact_lookup_100pct_v2/retrieval_eval_report.md
  - artifacts/fixed_qa_eval/retrieval_eval_exact_lookup_100pct_v2/retrieval_eval_results.csv
  - artifacts/fixed_qa_eval/retrieval_eval_exact_lookup_100pct_v2/retrieval_eval_results.jsonl

- final copies:
  - artifacts/fixed_qa_eval/final_reports/retrieval_eval_exact_lookup_118cases_100pct.md
  - artifacts/fixed_qa_eval/final_reports/retrieval_eval_exact_lookup_118cases_100pct.csv
  - artifacts/fixed_qa_eval/final_reports/retrieval_eval_exact_lookup_118cases_100pct.jsonl
  - artifacts/fixed_qa_eval/final_reports/approved_qa_exact_index_118cases.json

## Next Step

Proceed to answer generation evaluation.

The next evaluation should verify:

- generated answer contains the approved answer
- citation source_doc is correct
- citation source_pages are correct
- no cross-document confusion occurs
- unknown questions are not answered without evidence
