# chatbot

Citation-first RAG template for internal knowledge workflows.

This project is built for teams that need retrieval-backed answers, traceable evidence, and reproducible chunk preparation instead of opaque chatbot responses.

## What it does

- Ingests and prepares internal document chunks
- Supports retrieval and citation-backed answers
- Exposes FastAPI endpoints for chat and search
- Keeps chunk preparation reproducible for verification
- Provides scripts for chunk validation and maintenance

## Typical use cases

- Internal document Q&A
- Retrieval-backed support tools
- Citation-first knowledge workflows
- Small FastAPI-based RAG services
- Search and evidence inspection tools

## Stack

Python, FastAPI, Chroma, OpenAI-compatible APIs

## Why this repo matters

Many chat-style knowledge tools fail because users cannot inspect why an answer was produced. This repository prioritizes evidence, traceability, and reproducibility, making it more suitable for practical internal use.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn webapi.main:app --reload
```

## Notes

This repository is a good fit for teams that need:
- internal knowledge search
- citation-backed retrieval
- traceable RAG workflows
- API-first experimentation

## Eval runner (lightweight smoke)

`eval.runner` is a lightweight repo-native smoke evaluator for regression checks on the current grounded RAG path (retrieval, rerank, guard, fallback).

- Default mode is deterministic/local-friendly.
- Default mode uses deterministic stubbed generation.
- Default mode stubs vector retrieval to empty unless `--real-vector` is enabled.
- Default mode is not full live end-to-end answer quality evaluation.
- `--real-vector` and `--real-generation` are opt-in and may be less deterministic.

Run:

```bash
PYTHONPATH=. .venv/bin/python -m eval.runner \
  --cases eval/cases/smoke_cases.jsonl \
  --chunks-jsonl eval/cases/smoke_chunks.jsonl \
  --output runs/eval/smoke_results.json
```
