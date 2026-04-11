# chatbot

Citation-first Japanese RAG core for internal knowledge, support operations, and retrieval evaluation.

This repository is built around one practical requirement:

**return grounded answers with inspectable evidence, and fail conservatively when evidence is weak.**

It is not a generic chatbot demo.
It is a Japanese RAG implementation focused on retrieval quality, citation integrity, fallback safety, and reproducible evaluation.

---

## What this repository is for

Internal support and operations teams need answers they can trust.

In Japanese enterprise documents, relevant evidence is often fragmented across:

- short glossary entries
- FAQ fragments
- procedure sections
- policy/spec headings
- table rows
- code-like identifiers and mixed-script terms

This project provides a production-oriented retrieval stack that keeps citations first, makes fallback behavior explicit, and exposes enough trace information to debug retrieval and reranking failures.

---

## What makes this repo different

Many RAG demos stop at “answer generation works”.

This repository goes further:

- **citation-first answer path**
- **hybrid retrieval with Japanese-aware reranking**
- **guard and extractive fallback instead of opaque guessing**
- **doc-type-aware Japanese chunking**
- **traceable QA path for retrieval/rerank debugging**
- **repo-native evaluation, including retrieval-mode comparison**

This makes the repository useful not only as an answer service, but also as a testbed for improving Japanese retrieval behavior in a controlled, reproducible way.

---

## Why Japanese retrieval is hard

Japanese enterprise retrieval has several recurring failure modes:

- Mixed scripts (kanji, kana, alnum IDs) weaken naive lexical matching.
- Important terms are often very short (`PR2`, `請求書ID`, `承認フラグ`) and easy to confuse with supersets (`PR20`).
- Procedural evidence is distributed across neighboring lines and sections.
- Parent/child document structure matters for retrieval quality and answer readability.
- Table-like content is often semi-structured and not searchable as-is.
- Short but valid lookup queries are easy to over-block as “too general”.

This repository explicitly targets those failure modes.

---

## Core capabilities

- FastAPI endpoints for chat (`/chat`) and retrieval inspection (`/search`)
- Citation-first grounded answer flow with guard and extractive fallback
- Hybrid retrieval (`keyword + vector + fusion`)
- Japanese-aware reranking for:
  - short lookup queries
  - quoted terms
  - code-like identifiers
  - metadata-aware matches (title / section / alias / question)
- Parent/child chunk design for retrieval-vs-context separation
- Doc-type-aware Japanese chunk construction
- Lightweight deterministic smoke evaluation
- Retrieval-aware evaluation across:
  - `bm25_only`
  - `dense_only`
  - `hybrid`
  - `hybrid_rerank`

---

## Repository status

### Production-oriented core

These parts are already central to the repository and are intended to stay stable:

- citation-first answer path
- hybrid retrieval flow
- guard / fallback behavior
- FastAPI endpoints
- deterministic smoke evaluation workflow
- internal traceable QA path used by evaluation

### Evaluation-ready

These parts are already usable for controlled comparison and analysis:

- retrieval-aware eval runner
- labeled retrieval comparison cases
- gold doc / chunk labels
- abstain-labeled cases
- per-query JSONL output
- mode-level summary JSON output

### Still experimental / tuning surface

These parts are intentionally still treated as tuning knobs:

- chunk target sizes and heuristics
- parent expansion thresholds
- reranker boost strengths
- larger real-world benchmark coverage
- broader corpus realism beyond the small fixed smoke corpus

---

## Retrieval architecture

The answer pipeline is intentionally layered.

### 1. First-stage retrieval

Hybrid retrieval is used to gather candidate evidence from:

- keyword retrieval
- vector retrieval
- fused candidate ranking

### 2. Candidate rerank

Candidates are reranked using Japanese-aware lexical and metadata signals such as:

- quoted code-like terms
- alnum IDs
- katakana terms
- kanji terms
- short lookup cores
- title / section / alias / FAQ-question metadata

### 3. Context shaping

Retrieval units and answer-context units are not always the same.

- child chunks are useful for ranking precision
- parent chunks can be expanded later for grounded answer context

This allows the repository to keep retrieval precise while preserving enough context for citation-first answers.

### 4. Answering and validation

The final answer path:

- builds grounded evidence blocks
- generates citation-tagged output
- validates output shape
- uses extractive fallback when needed
- keeps fallback explicit instead of pretending confidence

---

## QA traceability

A core design goal of this repository is that retrieval failures should be inspectable.

The internal QA path exposes trace data that evaluation can reuse, including:

- before-rerank candidates
- after-rerank candidates
- after-parent-expansion candidates
- selected final answer context
- guard reason
- fallback usage
- rewritten / augmented query forms

This makes it possible to analyze where a result degraded:

- retrieval stage
- rerank stage
- grounding stage
- guard/fallback stage

That traceability is a major part of the repo’s value.

---

## Japanese chunking strategy

The repository includes `rag_core/chunking_ja.py` with document-type-aware chunk construction.

Supported chunking styles:

- **FAQ / glossary**
  - short chunks
  - one Q&A or one term-definition per unit

- **Procedure / how-to**
  - medium chunks
  - preserves prerequisites, steps, and notes

- **Policy / spec**
  - heading/section-oriented chunks
  - avoids brittle over-splitting of clauses

- **Table-like text**
  - flattened row chunks
  - keeps table title/header context in searchable form

### Default character targets

- FAQ / glossary: 80-300 chars
- Procedure / how-to: 300-900 chars
- Policy / spec: 400-1200 chars
- Table-like text: 80-500 chars

### Chunk metadata

Chunk records may include:

- `doc_type`
- `title`
- `section_path`
- `chunk_role`
- `parent_chunk_id`
- `child_chunk_ids`
- `searchable_text`
- `display_text`

plus backward-compatible fields such as:

- `doc_id`
- `source_doc`
- `source_pages`
- `chunk_index`
- `type`
- `quality`
- `searchable`

`searchable_text` is used for indexing and retrieval when present, while `display_text` is preserved for answer presentation.

---

## API surface

### `/chat`

Returns grounded answers with citations, guard behavior, and fallback handling.

Use this endpoint when you want the full answer pipeline.

### `/search`

Returns retrieval-oriented information and is useful for inspecting candidate evidence.

Use this endpoint when you want to inspect retrieval results more directly.

---

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn webapi.main:app --reload
Ingestion

Legacy fixed-window PDF chunking still works.
Japanese doc-type-aware chunking is available as an additive option.

Example:

PYTHONPATH=. .venv/bin/python scripts/pdf_to_canonical_jsonl.py \
  --pdf pdfs/your_doc.pdf \
  --out index/your_doc.jsonl \
  --doc-type procedure \
  --title "運用手順書"

Then ingest:

PYTHONPATH=. .venv/bin/python scripts/ingest_canonical_jsonl.py index/your_doc.jsonl --reset
Evaluation

eval.runner is a lightweight repo-native evaluator for retrieval, rerank, guard, and fallback regression checks.

It supports two distinct evaluation positions:

deterministic local-friendly evaluation
retrieval-aware comparison evaluation
Deterministic smoke evaluation

Deterministic mode is intended for regression safety.

Default behavior:

generation is stubbed
vector retrieval is stubbed empty unless --real-vector is enabled

This mode is good for:

expectation-based smoke checks
rerank movement checks
guard/fallback regressions
CI-friendly local validation

Run deterministic smoke:

PYTHONPATH=. .venv/bin/python -m eval.runner \
  --cases eval/cases/smoke_cases.jsonl \
  --chunks-jsonl eval/cases/smoke_chunks.jsonl \
  --output runs/eval/smoke_results.json
Retrieval-aware evaluation

Retrieval-aware evaluation compares baseline modes and saves:

per-query rows as JSONL
mode-level aggregate summary as JSON

Supported modes:

bm25_only
dense_only
hybrid
hybrid_rerank

Run retrieval-aware evaluation:

PYTHONPATH=. .venv/bin/python -m eval.runner \
  --retrieval-aware \
  --cases eval/cases/retrieval_cases.jsonl \
  --chunks-jsonl eval/cases/smoke_chunks.jsonl \
  --modes bm25_only,dense_only,hybrid,hybrid_rerank \
  --per-query-output runs/eval/retrieval_rows.jsonl \
  --summary-output runs/eval/retrieval_summary.json \
  --eval-k 5

Per-query rows include signals such as:

gold_doc_hit
gold_chunk_hit
best_rank_before_rerank
best_rank_after_rerank
rerank_gain
guard_reason
used_fallback
expected_abstain
abstain_correct

Mode-level summary includes:

gold_chunk_cases
gold_chunk_hits
gold_doc_cases
gold_doc_hits
abstain_labeled_cases
abstain_expected_cases
abstain_passes
mean_mrr_at_k
mean_ndcg_at_k
### Evaluation datasets

Case sets are intentionally separated by purpose:

eval/cases/smoke_cases.jsonl
lightweight regression checks
expectation-oriented
useful for deterministic smoke validation
eval/cases/retrieval_cases.jsonl
labeled retrieval comparison cases
includes gold IDs and abstain labels
useful for retrieval-mode comparison

### Deterministic vs real-vector evaluation

This repository supports both deterministic local-friendly evaluation and more realistic retrieval-quality comparison.

#### Deterministic default

Without --real-vector:

generation is stubbed unless --real-generation is enabled
vector retrieval is stubbed empty
keyword retrieval remains active

This mode is useful for:

local reproducibility
smoke regression checks
CI-friendly validation
guard/fallback consistency checks

This mode is not sufficient for interpreting dense retrieval quality.

#### Real-vector mode

Enable --real-vector when you want to evaluate whether vector retrieval is contributing meaningfully.

Example:

PYTHONPATH=. .venv/bin/python -m eval.runner \
  --retrieval-aware \
  --cases eval/cases/retrieval_cases.jsonl \
  --chunks-jsonl eval/cases/smoke_chunks.jsonl \
  --modes dense_only,hybrid,hybrid_rerank \
  --per-query-output runs/eval/retrieval_rows_real_vector.jsonl \
  --summary-output runs/eval/retrieval_summary_real_vector.json \
  --eval-k 5 \
  --real-vector

Recommended interpretation:

use deterministic mode for regression safety
use --real-vector for retrieval-quality comparison
do not over-interpret dense_only results from stub-vector mode as real embedding behavior
#### Real-generation mode

--real-generation can also be enabled, but answer-generation quality should be interpreted separately from retrieval comparison.

Retrieval comparison and answer-generation comparison should not be mixed casually in the same conclusion.

## Production-ready vs experimental

### Production-ready

citation-first answer path
hybrid retrieval flow
guard/fallback behavior
FastAPI endpoints
deterministic smoke evaluation workflow
### Experimental / tuning surface
doc-type heuristics in Japanese chunk construction
parent expansion limits
metadata boost tuning in reranker
larger benchmark coverage
corpus realism beyond the current fixed smoke corpus
Current limitations

This repository is strong as a controlled Japanese RAG core and retrieval evaluation base, but several limitations remain.

The retrieval comparison corpus is still relatively small.
Repo-native evaluation is useful, but it is not yet a full real-world benchmark.
Dense retrieval conclusions should be based on --real-vector runs, not only deterministic stub mode.
Chunking and reranker settings are intentionally still open to tuning.
This repo prioritizes inspectability and safety over broad chat behavior.
Suggested use cases

This repository is especially suitable for:

internal knowledge QA
support operations search
Japanese enterprise document retrieval experiments
citation-first answer systems
retrieval / rerank / fallback evaluation workflows

It is less suitable, in its current form, for:

general-purpose conversational chat
broad persona-driven assistants
benchmark claims beyond the current evaluation corpus
License

See [LICENSE](LICENSE).
