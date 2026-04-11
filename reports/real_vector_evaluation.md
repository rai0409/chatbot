## Deterministic vs Real-Vector Evaluation

This repository intentionally supports two different evaluation positions:

1. **deterministic local-friendly regression mode**
2. **real-vector retrieval comparison mode**

These two modes serve different purposes and should not be interpreted in the same way.

---

### 1. Deterministic local-friendly mode

Default behavior:

- generation is stubbed unless `--real-generation` is enabled
- vector retrieval is stubbed empty unless `--real-vector` is enabled
- keyword retrieval remains active

Purpose:

- local reproducibility
- regression testing
- guard / fallback validation
- rerank movement inspection
- CI-friendly smoke validation

This mode is appropriate when the goal is:

- “Did the retrieval/rerank/guard pipeline regress?”
- “Did fallback behavior change unexpectedly?”
- “Did a known smoke case stop passing?”
- “Did rerank-sensitive cases still move as expected?”

Example deterministic retrieval-aware evaluation:

```bash
cd /home/rai/chatbot && \
PYTHONPATH=/home/rai/chatbot /home/rai/chatbot/.venv/bin/python -m eval.runner \
  --retrieval-aware \
  --cases /home/rai/chatbot/eval/cases/retrieval_cases.jsonl \
  --chunks-jsonl /home/rai/chatbot/eval/cases/smoke_chunks.jsonl \
  --modes bm25_only,dense_only,hybrid,hybrid_rerank \
  --per-query-output /home/rai/chatbot/runs/eval/retrieval_rows_stub_vector.jsonl \
  --summary-output /home/rai/chatbot/runs/eval/retrieval_summary_stub_vector.json \
  --eval-k 5

This mode is not appropriate for making strong claims about dense retrieval quality.

In particular:

dense_only under stub-vector mode is not a meaningful proxy for real embedding retrieval
hybrid under stub-vector mode should be interpreted as a constrained local regression position, not as a realistic retrieval benchmark
deterministic smoke conclusions should not be presented as if they were production retrieval measurements
2. Real-vector retrieval comparison mode

Enable --real-vector when the goal is to evaluate whether vector retrieval contributes meaningfully.

Use this mode when you want to compare:

dense_only vs bm25_only
dense_only vs hybrid
hybrid vs hybrid_rerank
whether vector retrieval improves recall or rank quality
whether reranking still adds value after real vector retrieval is enabled

Full-path example:

cd /home/rai/chatbot && \
PYTHONPATH=/home/rai/chatbot /home/rai/chatbot/.venv/bin/python -m eval.runner \
  --retrieval-aware \
  --cases /home/rai/chatbot/eval/cases/retrieval_cases.jsonl \
  --chunks-jsonl /home/rai/chatbot/eval/cases/smoke_chunks.jsonl \
  --modes bm25_only,dense_only,hybrid,hybrid_rerank \
  --per-query-output /home/rai/chatbot/runs/eval/retrieval_rows_real_vector.jsonl \
  --summary-output /home/rai/chatbot/runs/eval/retrieval_summary_real_vector.json \
  --eval-k 5 \
  --real-vector

A narrower comparison focused on vector-sensitive modes is also reasonable:

cd /home/rai/chatbot && \
PYTHONPATH=/home/rai/chatbot /home/rai/chatbot/.venv/bin/python -m eval.runner \
  --retrieval-aware \
  --cases /home/rai/chatbot/eval/cases/retrieval_cases.jsonl \
  --chunks-jsonl /home/rai/chatbot/eval/cases/smoke_chunks.jsonl \
  --modes dense_only,hybrid,hybrid_rerank \
  --per-query-output /home/rai/chatbot/runs/eval/retrieval_rows_real_vector_dense_hybrid.jsonl \
  --summary-output /home/rai/chatbot/runs/eval/retrieval_summary_real_vector_dense_hybrid.json \
  --eval-k 5 \
  --real-vector

This is the correct evaluation position for discussing:

retrieval-quality differences involving dense retrieval
MRR / nDCG comparisons that depend on actual vector search
whether hybrid retrieval is truly outperforming keyword-only behavior
whether reranking still produces gains after realistic candidate retrieval
3. Real-generation mode

--real-generation can also be enabled, but answer-generation quality should be interpreted separately from retrieval comparison.

A fully live run looks like this:

cd /home/rai/chatbot && \
PYTHONPATH=/home/rai/chatbot /home/rai/chatbot/.venv/bin/python -m eval.runner \
  --retrieval-aware \
  --cases /home/rai/chatbot/eval/cases/retrieval_cases.jsonl \
  --chunks-jsonl /home/rai/chatbot/eval/cases/smoke_chunks.jsonl \
  --modes bm25_only,dense_only,hybrid,hybrid_rerank \
  --per-query-output /home/rai/chatbot/runs/eval/retrieval_rows_real_vector_real_generation.jsonl \
  --summary-output /home/rai/chatbot/runs/eval/retrieval_summary_real_vector_real_generation.json \
  --eval-k 5 \
  --real-vector \
  --real-generation

However, retrieval comparison and answer-generation comparison should not be mixed casually in the same conclusion.

A good rule is:

use --real-vector to discuss retrieval quality
use --real-generation only when you intentionally want to inspect answer behavior in addition to retrieval behavior
Recommended interpretation of results

Use the two modes differently.

Deterministic mode is for:
regression safety
local reproducibility
smoke checks
debugging guard/fallback behavior
verifying that rerank-sensitive cases still move as expected
Real-vector mode is for:
retrieval-quality comparison
dense retrieval usefulness
hybrid-vs-keyword comparison
reranker value after realistic candidate retrieval
Real-generation mode is for:
inspecting end-to-end answer behavior
checking whether grounded answer generation still behaves correctly under live settings
validating that retrieval gains translate into answer-side improvements
What not to do

Do not:

treat stub-vector dense_only as evidence of real dense retrieval quality
mix deterministic smoke conclusions and real-vector benchmark conclusions into one undifferentiated claim
use a small repo-native corpus to make overly broad external benchmark claims
present --real-generation output as if it were only a retrieval comparison result
Best-practice workflow

Recommended sequence:

run deterministic smoke evaluation first
confirm there are no regressions in guard/fallback and core expectations
run retrieval-aware evaluation with --real-vector
compare mode-level summary and query-type behavior separately
only after that, optionally run --real-generation
keep deterministic, real-vector, and real-generation conclusions clearly separated in reports

A practical command sequence is:
cd /home/rai/chatbot && \
PYTHONPATH=/home/rai/chatbot /home/rai/chatbot/.venv/bin/python -m eval.runner \
  --retrieval-aware \
  --cases /home/rai/chatbot/eval/cases/retrieval_cases.jsonl \
  --chunks-jsonl /home/rai/chatbot/eval/cases/smoke_chunks.jsonl \
  --modes bm25_only,dense_only,hybrid,hybrid_rerank \
  --per-query-output /home/rai/chatbot/runs/eval/retrieval_rows_stub_vector.jsonl \
  --summary-output /home/rai/chatbot/runs/eval/retrieval_summary_stub_vector.json \
  --eval-k 5

cd /home/rai/chatbot && \
PYTHONPATH=/home/rai/chatbot /home/rai/chatbot/.venv/bin/python -m eval.runner \
  --retrieval-aware \
  --cases /home/rai/chatbot/eval/cases/retrieval_cases.jsonl \
  --chunks-jsonl /home/rai/chatbot/eval/cases/smoke_chunks.jsonl \
  --modes bm25_only,dense_only,hybrid,hybrid_rerank \
  --per-query-output /home/rai/chatbot/runs/eval/retrieval_rows_real_vector.jsonl \
  --summary-output /home/rai/chatbot/runs/eval/retrieval_summary_real_vector.json \
  --eval-k 5 \
  --real-vector

cd /home/rai/chatbot && \
PYTHONPATH=/home/rai/chatbot /home/rai/chatbot/.venv/bin/python -m eval.runner \
  --retrieval-aware \
  --cases /home/rai/chatbot/eval/cases/retrieval_cases.jsonl \
  --chunks-jsonl /home/rai/chatbot/eval/cases/smoke_chunks.jsonl \
  --modes bm25_only,dense_only,hybrid,hybrid_rerank \
  --per-query-output /home/rai/chatbot/runs/eval/retrieval_rows_real_vector_real_generation.jsonl \
  --summary-output /home/rai/chatbot/runs/eval/retrieval_summary_real_vector_real_generation.json \
  --eval-k 5 \
  --real-vector \
  --real-generation
Practical conclusion

A good default interpretation is:

deterministic mode tells you whether the pipeline is stable
real-vector mode tells you whether retrieval quality is actually improving
real-generation mode tells you whether those retrieval differences carry through to end-to-end answer behavior

All three are useful, but they answer different questions.
They should be reported separately, not collapsed into one headline result.