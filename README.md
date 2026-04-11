# chatbot

Citation-first Japanese RAG service for internal knowledge and support operations.

This repository focuses on one practical goal: produce grounded answers with inspectable evidence instead of opaque chat responses.

## Problem this solves

Internal support and operations teams need answers they can trust. In Japanese enterprise documents, relevant facts are often split across headings, procedures, FAQ fragments, and table rows. This project provides a production-oriented retrieval stack that keeps citations first and fallback behavior explicit.

## Why Japanese retrieval is hard

- Mixed scripts (kanji, kana, alnum IDs) reduce naive token match quality.
- Important terms are often short (`PR2`, `請求書ID`, `承認フラグ`) and easy to confuse with supersets (`PR20`).
- Procedural evidence is distributed across neighboring lines and sections.
- Table content is frequently semi-structured and not searchable as-is.

## What the service provides

- FastAPI endpoints for chat (`/chat`) and retrieval inspection (`/search`)
- Citation-first grounded answer flow with guard and extractive fallback
- Hybrid retrieval (vector + keyword + RRF-style fusion)
- Japanese-aware metadata/rerank path for short lookup and code-like terms
- Lightweight deterministic eval runner for regression checks

## Retrieval architecture

1. First-stage retrieval: hybrid retrieval over vector + keyword candidates.
2. Candidate rerank: lexical + metadata-aware boosts (title/section/question/alias/code-aware).
3. Context shaping: child chunks are retrieval units; parent chunks can expand answer context after ranking.
4. Answering: grounded prompt with citation tags, validation, and fallback when needed.

## Japanese chunking strategy

The repository now includes `rag_core/chunking_ja.py` with document-type-aware chunk construction:

- FAQ / glossary: short chunks, one Q&A or one term-definition per unit
- Procedure / how-to: medium chunks, preserves prerequisites + steps + notes
- Policy / spec: heading/section-oriented chunks to avoid brittle clause splits
- Table-like text: flattened row chunks with table title/header context

Default character targets:

- FAQ / glossary: 80-300 chars
- Procedure / how-to: 300-900 chars
- Policy / spec: 400-1200 chars

Chunk metadata includes:

- `doc_type`, `title`, `section_path`, `chunk_role`
- `parent_chunk_id`, `child_chunk_ids`
- `searchable_text`, `display_text`
- plus backward-compatible fields such as `doc_id`, `source_doc`, `source_pages`, `chunk_index`, `type`, `quality`, `searchable`

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn webapi.main:app --reload
```

## Ingestion

Legacy fixed-window PDF chunking still works. Japanese doc-type-aware chunking is available as an additive option.

```bash
PYTHONPATH=. .venv/bin/python scripts/pdf_to_canonical_jsonl.py \
  --pdf pdfs/your_doc.pdf \
  --out index/your_doc.jsonl \
  --doc-type procedure \
  --title "運用手順書"
```

Then ingest:

```bash
PYTHONPATH=. .venv/bin/python scripts/ingest_canonical_jsonl.py index/your_doc.jsonl --reset
```

`searchable_text` is used for indexing/retrieval when present, while `display_text` is preserved for answer presentation.

## Evaluation

`eval.runner` is a lightweight repo-native smoke evaluator for retrieval/rerank/guard/fallback regression checks.

- Deterministic default: stubbed generation and local-friendly behavior
- Optional live toggles: `--real-vector`, `--real-generation`
- Supports expectations on top hit, guard/fallback, answer constraints, and selected context checks

Run deterministic smoke:

```bash
PYTHONPATH=. .venv/bin/python -m eval.runner \
  --cases eval/cases/smoke_cases.jsonl \
  --chunks-jsonl eval/cases/smoke_chunks.jsonl \
  --output runs/eval/smoke_results.json
```

Retrieval-aware evaluation (Phase 1, additive) can run baseline modes and save both per-query rows (JSONL) and mode-level summary (JSON):

```bash
PYTHONPATH=. .venv/bin/python -m eval.runner \
  --retrieval-aware \
  --cases eval/cases/smoke_cases.jsonl \
  --chunks-jsonl eval/cases/smoke_chunks.jsonl \
  --modes bm25_only,dense_only,hybrid,hybrid_rerank \
  --per-query-output runs/eval/retrieval_rows.jsonl \
  --summary-output runs/eval/retrieval_summary.json \
  --eval-k 5
```

Per-query rows include retrieval hit/rank signals (`gold_doc_hit`, `gold_chunk_hit`, `best_rank_before_rerank`, `best_rank_after_rerank`, `rerank_gain`) and guard/abstain signals (`guard_reason`, `used_fallback`, `expected_abstain`, `abstain_correct`).

## Production-ready vs experimental

Production-ready:

- Citation-first answer path
- Hybrid retrieval flow
- Guard/fallback behavior
- FastAPI endpoints
- Deterministic smoke eval workflow

Experimental / tuning surface:

- Doc-type heuristics in Japanese chunk construction
- Parent expansion limits (`ENABLE_PARENT_EXPANSION`, `MAX_PARENT_CONTEXT_CHARS`)
- Metadata boost strength in reranker

## License

See [LICENSE](LICENSE).
