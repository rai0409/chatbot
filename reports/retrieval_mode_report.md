# Retrieval Mode Evaluation Report

## Scope

This report compares retrieval behavior across:

- `bm25_only`
- `dense_only`
- `hybrid`
- `hybrid_rerank`

Dataset:
- `eval/cases/retrieval_cases.jsonl`

Corpus:
- `eval/cases/smoke_chunks.jsonl`

Evaluation:
- `eval_k = 5`

## Why this report exists

The goal is not only to check whether the repo returns answers.

The goal is to measure where each retrieval mode helps or fails for Japanese citation-first RAG:
- short identifier lookup
- quoted term lookup
- lexical Japanese queries
- broad semantic queries
- procedure questions
- abstain-expected cases

## Aggregate summary by mode

| mode | cases | gold_chunk_hits | gold_doc_hits | mean_mrr_at_k | mean_ndcg_at_k | abstain_labeled_cases | abstain_expected_cases | abstain_passes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| bm25_only |  |  |  |  |  |  |  |  |
| dense_only |  |  |  |  |  |  |  |  |
| hybrid |  |  |  |  |  |  |  |  |
| hybrid_rerank |  |  |  |  |  |  |  |  |

## Query-type breakdown

### exact_identifier
- strongest mode:
- weakest mode:
- notes:

### quoted_term
- strongest mode:
- weakest mode:
- notes:

### ja_lexical
- strongest mode:
- weakest mode:
- notes:

### broad_semantic
- strongest mode:
- weakest mode:
- notes:

### procedure
- strongest mode:
- weakest mode:
- notes:

### abstain_expected
- strongest mode:
- weakest mode:
- notes:

## Rerank-sensitive findings

List cases where:
- `best_rank_after_rerank < best_rank_before_rerank`
- or `rerank_top_changed == true`

Example table:

| case_id | query_type | before_rank | after_rank | rerank_gain | interpretation |
|---|---|---:|---:|---:|---|
|  |  |  |  |  |  |

## Abstain behavior

### Good abstentions
- cases where `expected_abstain = true` and `abstain_correct = true`

### Incorrect abstentions
- false positive fallback cases

### Missed abstentions
- false negative non-fallback cases

## Interpretation

### What the current stack is good at
- 
- 
- 

### What is still weak
- 
- 
- 

### Practical conclusion
- default recommended mode:
- when to prefer hybrid over hybrid_rerank:
- current limitation of dense-only:
- why this is still a repo-native benchmark, not a full real-world benchmark: