# Unknown Abstention Evaluation Report

## Executive Summary
- status: passed
- total_cases: 32
- errors: 0
- abstained_count: 32
- grounded_answer_count: 0
- unsupported_answer_count: 0
- approved_exact_false_positive_count: 0

## Evaluation Result
- citations_count: 29
- classification_distribution: `{"abstained": 32}`
- used_fallback_distribution: `{"False": 10, "True": 22}`

## Guard Reason Distribution
- (empty): 11
- salient_mismatch: 4
- soft_distance: 5
- too_general: 12

## Unsupported Answer Cases
- none

## Error Cases
- none

## Commercial Judgment
- approved_qa_exact false positive is acceptable only at 0.
- Unknown questions should abstain or return clearly grounded answers with citations.
- judgment: commercial abstention gate passed

## Next Steps
- Review every unsupported case and decide whether to improve guardrails, retrieval thresholds, or abstention policy.
- Add these unknown questions to a recurring regression suite.
- Keep exact QA, unknown abstention, and normal retrieval evaluations as separate gates.
