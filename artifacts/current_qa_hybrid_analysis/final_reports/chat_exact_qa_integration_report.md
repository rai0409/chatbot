# Chat Approved QA Exact Integration Report

## Executive Summary

- `/chat` is now connected to the 118-case approved QA exact index.
- `/chat/stream` is also connected to the same exact index.
- Existing `_approved_qa_lookup()` remains first in the lookup order.
- `staging_collection` requests still bypass approved exact lookup.
- Live `/chat` checks for `qa_pdf_0015`, `biscfaq_table_qa_0026`, and `biscfaq_table_qa_0042` returned `answer_mode=approved_exact_match` and `retrieval_source=approved_qa_exact`.
- The 118-case live `/chat` evaluation passed.

## Implementation

Changed files:

- `webapi/main.py`
- `tools/evaluate_chat_approved_qa.py`

Added helpers:

- `_approved_exact_hit_to_chat_payload(hit, question, tenant_id)`
- `_approved_exact_chat_lookup(question, tenant_id)`

`/chat` processing order:

1. Existing `_approved_qa_lookup()`
2. New `_approved_exact_chat_lookup()`
3. answer cache
4. `answer_query_with_trace()`

`/chat/stream` processing order:

1. Existing `_approved_qa_lookup()`
2. New `_approved_exact_chat_lookup()`
3. streaming generation path

The exact payload adds `answer`, `abstained=false`, `answer_mode=approved_exact_match`, `retrieval_source=approved_qa_exact`, `tenant_id`, `query_collection_mode`, and `query_collection` while preserving existing response fields such as `answer_text`, `answer_with_footnotes`, `citations`, `guard_reason`, and `used_fallback`.

## Evidence

Compile:

```bash
python -m py_compile webapi/main.py tools/evaluate_chat_approved_qa.py
```

Live `/chat` checked cases:

- `qa_pdf_0015`: `answer_mode=approved_exact_match`, `retrieval_source=approved_qa_exact`, source `58887_95105_misc.pdf`
- `biscfaq_table_qa_0026`: `answer_mode=approved_exact_match`, `retrieval_source=approved_qa_exact`, source `040219e-biscfaq.pdf`
- `biscfaq_table_qa_0042`: `answer_mode=approved_exact_match`, `retrieval_source=approved_qa_exact`, source `040219e-biscfaq.pdf`

Live `/chat/stream` check:

- returned `event: approved`
- payload included `answer_mode=approved_exact_match`
- payload included `retrieval_source=approved_qa_exact`

## Evaluation Result

Command:

```bash
.venv/bin/python tools/evaluate_chat_approved_qa.py \
  --cases artifacts/fixed_qa_eval/fixed_qa_cases.jsonl \
  --chat-url http://127.0.0.1:8000/chat \
  --output-dir artifacts/current_qa_hybrid_analysis/chat_exact_qa_eval
```

Result:

- total_cases: 118
- errors: 0
- answer_match_rate: 1.000
- source_doc_match_rate: 1.000
- approved_exact_rate: 1.000
- llm_fallback_count: 0
- failed cases: none

Output:

- `artifacts/current_qa_hybrid_analysis/chat_exact_qa_eval/chat_exact_qa_eval_results.jsonl`
- `artifacts/current_qa_hybrid_analysis/chat_exact_qa_eval/chat_exact_qa_eval_results.csv`
- `artifacts/current_qa_hybrid_analysis/chat_exact_qa_eval/chat_exact_qa_eval_report.md`

## Commercial Judgment

`/chat` approved QA exact is usable for deterministic approved-answer serving for the current 118 cases.

Still missing:

- unknown abstention evaluation
- BM25 keyword index restoration
- `/search` fallback hybridization
- approved QA similar retrieval and confidence thresholding

## Next Steps

1. Add unknown abstention eval.
2. Restore and verify BM25 keyword index loading.
3. Make `/search` fallback hybrid or explicitly split vector-only and hybrid endpoints.
4. Add approved QA similar retrieval with a question-text index and threshold evaluation.
