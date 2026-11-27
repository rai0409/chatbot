# Retrieval benchmarking (eval.bench)

`python -m eval.bench` compares retrieval pipelines (dense, BM25, weighted RRF, DBSF, cross-encoder rerank)
on one evaluation set under identical conditions. Heavy runs are never started automatically: the commands
in this page are written for a person to start, and a result that was not measured is reported as UNEXECUTED.

## Ranking is deterministic

The fused ranking orders candidates by child-chunk priority, fused score and lexical presence. Equal keys are
ordered by chunk key (production: `rag_core.retrieval._rank_buckets`) or chunk id (bench: `fuse_one(..., ids=...)`),
never by the order in which the dense and lexical lanes returned their hits. The same candidates give the same ranking.
Tenant filters are applied inside each lane and are unchanged.

## The test split is used once

Tune on split `dev`. Split `test` is the held-out estimate:

- `runner.run` refuses split `test` unless the config has `final_test: true` (CLI: `--final-test`).
- Every use is written to a ledger (`runs/bench/test_access.jsonl`, or `test_ledger` in the config), keyed by the SHA-256 of the evaluation set.
- A second use of the same evaluation set is refused unless `allow_test_reuse: true` (CLI: `--allow-test-reuse`); the re-use is recorded.
- Numbers that were chosen on a data set (for example a lab's exploratory set) are exploration only. Do not report them as the accuracy of this product or carry them over to a held-out set.

The ledger protects against accidents; it is a local file, not an audit log.

## Evaluation sets from the labelled cases

```
python -m eval.bench from-cases --cases eval/cases/semantic_challenge_cases.jsonl --out runs/eval_set.jsonl --test-share 0.2 --seed 0
```

The split is decided by a hash of the document group, so questions about one document stay together and the result is reproducible.
Slices: `cat:<category>` plus heuristic tags found in the query (`year`, `amount`, `negation`, `condition`, `code`, `katakana`, `short`).
The tags help to look at results per kind of question; they are not gold labels.

## What a reranker can and cannot do

A cross-encoder only reorders the candidates it receives. A relevant chunk that is not among the first `depth` candidates cannot be rescued by it.

## What a run records (3B)

Every run writes `run.json` and, per configuration, a line in `summary.jsonl`:

- evaluation set SHA-256, split, number of queries, match mode, tie-break rule, git commit;
- the corpus SHA-256 and the embedding profile (model, revision, prefix scheme, normalization, distance, dimension) of every configuration;
- the index generation (`generation_id`, collection, counts, verification status) when the config names one;
- hit@1/3/5/10, recall@20/50, MRR@10 and nDCG@10 (config `ks` must contain 1, 3, 5, 10, 20, 50; `required_metrics_missing` in `run.json` lists what is absent),
  each overall and per slice, with bootstrap confidence intervals;
- latency p50 / p95 per stage and in total (the rerank stage is an estimate from the measured seconds per pair);
- `candidate_miss_rate`: the share of answerable questions with no relevant evidence among the candidates
  (the first `depth` for a reranked configuration, else the first `max(ks)`). A reranker cannot rescue these questions.

### Using an index generation

`"generation": "<collection>"` in the config resolves a generation registered with `scripts/switch_generation.py register`.
Its keyword corpus becomes the corpus and its embedding profile the default embedding.
A config that names another corpus, model or prefix scheme is refused, so a run is always about the generation it names.

### Comparing runs

`python -m eval.bench.compare_runs ... --require-same-conditions` refuses to compare runs that differ in evaluation set,
split, corpus, embedding profile or generation (or that did not record them). Without the flag the report still lists the
differences under `conditions`. The report also lists the questions the candidate answers better (`improved`) and worse (`worsened`).
Compare retrieval methods (dense, BM25, RRF, DBSF, rerank) on one run or on runs with the same conditions; a corpus or
profile change is a different experiment (use evidence match mode, because chunk ids change with the chunking).

## Large runs: commands for a person to start (UNEXECUTED)

None of these commands is run by the test suite, and nothing in this page has been measured. Until someone runs them,
every accuracy statement about the real corpus is UNEXECUTED.

```
python scripts/switch_generation.py register --collection <collection>
python -m eval.bench from-cases --cases <labelled cases>.jsonl --out runs/eval_set.jsonl
python -m eval.bench plan --config <config>.json
python -m eval.bench run --config <config>.json            # loads the embedding and reranker models (downloads them if absent)
python -m eval.bench compare --run runs/bench/<run> --baseline <config_id> --metric recall@10
python -m eval.bench.compare_runs --baseline-run runs/bench/<A> --baseline-config <id> \
    --candidate-run runs/bench/<B> --candidate-config <id> --require-same-conditions
python -m eval.bench run --config <config with "split": "test">.json --final-test   # once, at the very end
```

A configuration is promoted to a default only after it passes the comparison on `dev` and the single `test` run.

## HYBRID_PARALLEL: measure before enabling

`HYBRID_PARALLEL` runs the dense and keyword lanes together. On a CPU-bound host (embedding threads plus lane workers
competing for the same cores) it can be slower. Do not enable it from reasoning; run the measurement on the production PC:

```
python scripts/measure_hybrid_parallel.py --queries <queries>.jsonl --n 100 \
    --workers 4 --peak 8 --expected-concurrency 4 --output reports/bench/hybrid_parallel/<run>.json
```

It runs hybrid retrieval for N queries at concurrency 1, `workers` and `peak`, with HYBRID_PARALLEL off and on, and prints
p50/p95 latency and throughput per level plus a recommendation. Rule: enable only if p95 improves by at least 15 percent
at the expected concurrency AND throughput does not drop by more than 5 percent. Otherwise keep it `false`.
Status until someone runs it: UNEXECUTED (the tests use stub lanes only).

Startup guard: with HYBRID_PARALLEL on, the first hybrid call logs one warning when `EMBED_NUM_THREADS +
HYBRID_PARALLEL_WORKERS` exceeds the physical cores (`os.cpu_count() // 2`).

## Statistics guards (compare tooling)

- `--resamples` must be 1000 or more in `eval.rerank_promotion_gate`, `eval.bench.compare_runs` and `eval.bench jqara-compare` (an error otherwise).
- Several candidates against one baseline: `python -m eval.bench compare` applies Holm correction (column `Holm p`).
  `eval.rerank_promotion_gate` takes repeated `--candidate-config` and requires `--primary`; the other candidates are
  reported as `exploratory` and can never promote.

## Broad-except lint guard

`ruff.toml` plus the CI step `ruff_broad_except` run `ruff check --select BLE001,S110` on the Python files changed against
`origin/main` (`scripts/lint_changed_broad_except.py`, findings on untouched legacy lines are ignored). A broad
`except Exception` that stays on purpose (best effort, logged, fail closed) is written
`except Exception:  # noqa: BLE001  <short reason>`; otherwise narrow it.
