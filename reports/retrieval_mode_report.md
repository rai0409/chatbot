# Retrieval Mode Evaluation Report

## Scope

This report compares retrieval behavior across:

- `bm25_only`
- `dense_only`
- `hybrid`
- `hybrid_rerank`

Dataset:
- `eval/cases/retrieval_cases.jsonl` (25 cases)

Corpus:
- `eval/cases/smoke_chunks.jsonl`

Evaluation:
- `eval_k = 5`

## Evidence sources

1. Runner implementation: `eval/runner.py` (`run_retrieval_aware_eval`)
2. Local run output (deterministic, stub-vector):
   - `/tmp/chatbot_phase2_rows.jsonl`
   - `/tmp/chatbot_phase2_summary.json`
3. Local aggregation over `/tmp/chatbot_phase2_rows.jsonl`

Command used:

```bash
cd /home/rai/chatbot && \
PYTHONPATH=/home/rai/chatbot .venv/bin/python -m eval.runner \
  --retrieval-aware \
  --cases eval/cases/retrieval_cases.jsonl \
  --chunks-jsonl eval/cases/smoke_chunks.jsonl \
  --modes bm25_only,dense_only,hybrid,hybrid_rerank \
  --per-query-output /tmp/chatbot_phase2_rows.jsonl \
  --summary-output /tmp/chatbot_phase2_summary.json \
  --eval-k 5 \
  --quiet
```

Run timestamp (`generated_at` in summary): `2026-04-12T01:46:58.107095+00:00`

Real-vector rerun status:
- **Unavailable in this environment** (`RuntimeError: OPENAI_API_KEY is missing`)
- No real-vector metrics are filled in this report.

## Aggregate summary by mode

| mode | cases | gold_chunk_hits / cases | gold_doc_hits / cases | mean_mrr_at_k | mean_ndcg_at_k | abstain_labeled_cases | abstain_expected_cases | abstain_passes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| bm25_only | 25 | 19 / 19 | 20 / 20 | 0.950 | 0.985 | 25 | 5 | 23 |
| dense_only | 25 | 0 / 19 | 0 / 20 | N/A | 0.000 | 25 | 5 | 5 |
| hybrid | 25 | 19 / 19 | 20 / 20 | 0.950 | 0.985 | 25 | 5 | 23 |
| hybrid_rerank | 25 | 19 / 19 | 20 / 20 | 0.975 | 1.003* | 25 | 5 | 23 |

\* `mean_ndcg_at_k > 1.0` is observed in current output. This comes from current metric behavior in the runner and is reported as-is (not corrected in this PR).

## Query-type breakdown (deterministic run)

Per query type, the table below summarizes hit behavior and notable differences.

| query_type | cases | strongest mode(s) from measured output | weakest mode | evidence summary |
|---|---:|---|---|---|
| `exact_identifier` | 5 | `bm25_only`, `hybrid`, `hybrid_rerank` | `dense_only` | strong modes: chunk/doc hit 5/5, mean MRR 1.0; dense_only: no gold hits |
| `quoted_term` | 4 | `hybrid_rerank` | `dense_only` | `hybrid_rerank` improves mean MRR from 0.875 to 1.0; dense_only no gold hits |
| `ja_lexical` | 4 | `bm25_only`, `hybrid`, `hybrid_rerank` | `dense_only` | strong modes: chunk/doc hit 4/4; dense_only no gold hits |
| `broad_semantic` | 2 | tie (`bm25_only`, `hybrid`, `hybrid_rerank`) | `dense_only` | non-dense modes hit 1/1 labeled gold case and abstain_correct 1/2 |
| `procedure` | 3 | `bm25_only`, `hybrid`, `hybrid_rerank` | `dense_only` | strong modes: chunk/doc hit 3/3; dense_only no gold hits |
| `rerank_sensitive` | 2 | tie (`bm25_only`, `hybrid`, `hybrid_rerank`) | `dense_only` | all non-dense modes 2/2 hits; rerank gain appears in one case (below) |
| `doc_level_only` | 1 | tie (`bm25_only`, `hybrid`, `hybrid_rerank`) | `dense_only` | doc hit 1/1 for non-dense modes; dense_only no hit |
| `abstain_expected` | 4 | all modes equal | none | abstain_correct 4/4 for all modes |

## Rerank-sensitive findings

Cases where rerank clearly improved rank in `hybrid_rerank`:

| case_id | query_type | before_rank | after_rank | rerank_gain | interpretation |
|---|---|---:|---:|---:|---|
| `r_quoted_qx12` | `quoted_term` | 2 | 1 | +1 | reranker moved quoted exact identifier (`QX12`) above competing candidate |

No cases with negative rerank gain were observed in this deterministic run.

## Abstain behavior

### Good abstentions

For `hybrid_rerank`, `expected_abstain=true` and `abstain_correct=true` for 5 cases:

- `r_broad_policy_short_guard`
- `r_abstain_kore`
- `r_abstain_imi`
- `r_abstain_unyo`
- `r_abstain_unknown_code`

### Incorrect abstentions (false positives)

For `hybrid_rerank`, 2 cases had `expected_abstain=false` but `used_fallback=true`:

- `r_quoted_form_type`
- `r_broad_policy_overview`

### Missed abstentions (false negatives)

No false negatives observed in this deterministic run (`expected_abstain=true` with `used_fallback=false`: 0).

## Interpretation

### What this measured run supports

- In deterministic (stub-vector) mode, `bm25_only`, `hybrid`, and `hybrid_rerank` behave similarly on hit coverage.
- `hybrid_rerank` shows measurable gain on quoted-term sensitivity (`quoted_term` MRR improvement).
- Abstain handling is mostly consistent but still has false-positive fallback on 2 labeled non-abstain cases.

### What this measured run does not support

- It does **not** support conclusions about real dense retrieval quality because `--real-vector` could not be executed in this environment.

### Practical conclusion

- For this deterministic corpus, `hybrid_rerank` remains the best default among the tested modes.
- Dense-vs-hybrid claims should be revisited only after a successful `--real-vector` run with valid API credentials.
