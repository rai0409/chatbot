# Minimal RAG Template (Chroma + OpenAI)

This repository provides a minimal retrieval‑augmented generation (RAG) template with:
- CLI for interactive questions
- FastAPI web API (`/chat`, `/search`)
- Grounded answers with citations converted to footnotes
- Scripts for validating and preparing canonical JSONL chunks

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create `.env` in the repo root:

```
OPENAI_API_KEY=...
```

## Key Environment Variables

- `OPENAI_API_KEY`: required
- `OPENAI_BASE_URL`: optional, e.g. for proxy
- `CHAT_MODEL`: default `gpt-4o-mini`
- `EMBED_MODEL`: default `text-embedding-3-small`
- `VECTORSTORE_DIR`: default `vectorstore/`
- `VECTORSTORE_COLLECTION_NAME`: default `maru20_chunks_v5`
- `TOP_K`: default `20`
- `MAX_CONTEXT_CHARS`: default `8000`
- `EMBED_PROVIDER`: `openai` (default) or `local` (requires sentence-transformers)

Guard/intent thresholds are configurable in `.env` as well (see `config.py`).

## CLI Usage

```bash
python rag_cli.py
```

Batch mode (stdin not TTY):

```bash
printf "質問1\n質問2\n" | python rag_cli.py
```

## Web API

```bash
uvicorn webapi.main:app --reload
```

Endpoints:
- `POST /chat` → `{ "answer": "..." }`
- `POST /search` → `{ "hits": [ { "text", "metadata", "score" } ] }`
- `GET /health`
- `GET /metrics`

## Scripts

- `scripts/assert_no_terms_in_chunks.py`: exit code 2 if any chunk has type `term`/`terms`
- `scripts/check_chunks_canonical.py`: print canonical JSONL stats
- `tools/dedup_chunks_by_id.py`: deduplicate JSONL and emit CSV report

### Note about ingest script mismatch

`scripts/ingest_canonical_jsonl.py` intentionally references APIs that do not exist in this repo to mirror a mixed-version script. It is safe because it only runs under `__main__`.
