# chatbot

Citation-first Japanese RAG core for internal knowledge, support operations, approved Q&A exact-match answers, and retrieval evaluation.

This repository is built around one practical requirement:

**return grounded answers with inspectable evidence, reuse approved answers deterministically when available, and fail conservatively when evidence is weak.**

It is not a generic chatbot demo.
It is a Japanese commercial RAG implementation focused on retrieval quality, citation integrity, approved-answer governance, fallback safety, and reproducible evaluation.

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
- official Q&A tables where each row has a question and an answer

This project provides a production-oriented retrieval stack that keeps citations first, makes fallback behavior explicit, exposes enough trace information to debug retrieval and reranking failures, and supports approved Q&A records for deterministic same-question answers.

---

## What makes this repo different

Many RAG demos stop at “answer generation works”.

This repository goes further:

- **approved exact-match answer path**
- **citation-first answer path**
- **hybrid retrieval with Japanese-aware reranking**
- **query-type classification and keyword score diagnostics**
- **conservative keyword boost for exact lookup / identifier queries**
- **guard and extractive fallback instead of opaque guessing**
- **doc-type-aware Japanese chunking**
- **Q&A table PDF extraction into approved-QA JSONL**
- **approved-QA review / promote / export workflow**
- **traceable QA path for retrieval/rerank debugging**
- **repo-native evaluation, including retrieval-mode comparison and approved-QA regression**

This makes the repository useful not only as an answer service, but also as a testbed for improving Japanese retrieval behavior in a controlled, reproducible way.

---

## Commercial answer routing policy

The production answer route is intentionally layered.

### 1. `approved_exact_match`

If the normalized user question exactly matches an approved Q&A record, `/chat` returns the approved answer directly.

This path:

- does not call the LLM
- does not run retrieval
- returns `answer_mode=approved_exact_match`
- returns `approved_qa_id`
- preserves approved citations
- is deterministic for the same approved question

This is the safest route for official FAQ, public Q&A, bid-question answers, support templates, and answers that must not drift.

### 2. Approved Q&A candidate search

Future work should search approved Q&A questions for similar user questions before falling back to normal RAG.

This should initially be debug-only and should not auto-answer until thresholds, margins, and keyword evidence have been validated.

### 3. Q+A pair RAG

Approved Q&A records should also be converted into canonical Q+A pair chunks for RAG so that retrieval sees the question and answer together.

This is preferred for table-style Q&A documents because generic PDF chunking may split the question and answer into separate chunks.

### 4. Normal document RAG

If no approved exact match exists, the chatbot uses the normal grounded RAG path:

- retrieval
- reranking
- parent expansion
- citation-first answer generation
- guard / fallback when evidence is weak

---

## Current verified production milestone

The current approved-QA flow has been verified on `58887_95105_misc.pdf`, a Japanese table-style Q&A PDF for:

> 観光デジタルアンケート分析業務 質問に対する回答

Verified result:

- table-style PDF Q&A extraction: **22 draft Q&A records**
- draft validation: **0 errors**
- approved-QA runner: **22/22, pass_rate=1.0**
- `/chat` with production path `data/approved_qa/default.jsonl`: **22/22 exact same-question approved_answer match**
- vector ingest of canonical PDF chunks: **104 records ingested, skipped=0**

This confirms that same-question approved answers can be served deterministically through the API.

---

## Repository status

### Production-oriented core

These parts are already central to the repository and are intended to stay stable:

- approved exact-match answer path
- citation-first answer path
- hybrid retrieval flow
- guard / fallback behavior
- FastAPI endpoints
- deterministic smoke evaluation workflow
- internal traceable QA path used by evaluation
- approved-QA runner for same-question same-answer regression

### Evaluation-ready

These parts are already usable for controlled comparison and analysis:

- retrieval-aware eval runner
- labeled retrieval comparison cases
- gold doc / chunk labels
- abstain-labeled cases
- per-query JSONL output
- mode-level summary JSON output
- `/search/debug` retrieval-only mode
- query type and keyword score details
- approved-QA exact-match regression

### Operationally ready

These workflows are now usable for commercial-style operation:

- PDF to canonical JSONL
- canonical JSONL to vectorstore
- table-style Q&A PDF to draft approved-QA JSONL
- approved-QA review / promote / reject / export
- approved-QA exact-match `/chat` response
- approved-QA production path validation

### Still experimental / tuning surface

These parts are intentionally still treated as tuning knobs:

- chunk target sizes and heuristics
- parent expansion thresholds
- reranker boost strengths
- approved similar-match thresholds
- larger real-world benchmark coverage
- broader corpus realism beyond the small fixed smoke corpus
- automatic Q&A extraction quality across many PDF table layouts

---

## Retrieval architecture

The answer pipeline is intentionally layered.

### 1. Approved exact match

When `APPROVED_QA_ENABLED=true`, `/chat` first checks the approved-QA index.

If an exact normalized question match is found:

- `answer_text` is the approved answer
- `answer_mode` is `approved_exact_match`
- `approved_qa_id` is returned
- citations are preserved from approved Q&A metadata
- `retrieved` is empty
- no LLM generation is required

### 2. First-stage retrieval

If no approved exact match exists, hybrid retrieval gathers candidate evidence from:

- keyword retrieval
- vector retrieval
- fused candidate ranking

### 3. Candidate rerank

Candidates are reranked using Japanese-aware lexical and metadata signals such as:

- quoted code-like terms
- alnum IDs
- katakana terms
- kanji terms
- short lookup cores
- title / section / alias / FAQ-question metadata
- query type
- keyword score details

### 4. Context shaping

Retrieval units and answer-context units are not always the same.

- child chunks are useful for ranking precision
- parent chunks can be expanded later for grounded answer context
- approved Q&A pair chunks should be used when Q and A must be searched together

This allows the repository to keep retrieval precise while preserving enough context for citation-first answers.

### 5. Answering and validation

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
- query type
- keyword score
- matched terms and matched fields
- keyword boost diagnostics
- approved exact-match metadata when used

This makes it possible to analyze where a result degraded:

- approved-QA lookup stage
- retrieval stage
- rerank stage
- grounding stage
- guard/fallback stage

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

- **Approved Q&A**
  - one record should preserve one question and one answer
  - exact-match records are stored as approved-QA JSONL
  - RAG records should be converted to Q+A pair chunks

### Default character targets

- FAQ / glossary: 80-300 chars
- Procedure / how-to: 300-900 chars
- Policy / spec: 400-1200 chars
- Table-like text: 80-500 chars
- Approved Q&A pair chunks: one question + one approved answer

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
- `qa_id`
- `normalized_question`

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

Returns approved exact-match answers or grounded RAG answers with citations, guard behavior, and fallback handling.

Use this endpoint when you want the full answer pipeline.

When approved exact match is active, the response may include:

- `answer_mode: approved_exact_match`
- `approved_qa_id`
- `normalized_question`
- approved citations
- empty `retrieved`

### `/search`

Returns retrieval-oriented information and is useful for inspecting candidate evidence.

Use this endpoint when you want to inspect retrieval results more directly.

### `/search/debug`

Returns compact trace information for retrieval debugging.

Use this endpoint when you want to inspect:

- query type
- before/after rerank candidates
- parent expansion
- keyword score details
- compact previews
- retrieval-only behavior without LLM generation

---

## Setup

### Free / local-only chat mode

Set `CHAT_GENERATION_MODE=extractive` to run `/chat` without OpenAI chat completion. With `EMBED_PROVIDER=local`, the service can be tested without an OpenAI API key while preserving the normal retrieval path.

In this mode, `/chat` still returns approved exact matches deterministically, otherwise it returns grounded extractive answers when retrieved evidence is sufficient, or an explicit abstain/fallback answer when evidence is weak.

```bash
bash scripts/run_free_extractive_chat_mode_check.sh
```

Expected gates:

- exact QA: `118/118`
- unknown abstention: `32/32`
- normal retrieval: `hybrid_hit@5=1.0`

The candidate collection `chatbot_chunks_v1_aligned_candidate` has been evaluated for this mode. Promotion is not automatic; use `artifacts/free_extractive_chat_mode/promotion_gate.md` for the gate and `artifacts/free_extractive_chat_mode/controlled_production_switch_plan.md` for the config-only switch plan.

### Local environment

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn webapi.main:app --reload
```

### Local development port

To avoid conflicts with other local projects using port `8000`, this project can be run on port `8010` during local development.

```bash
uvicorn webapi.main:app --host 127.0.0.1 --port 8010
```

Health check:

```bash
curl -s http://127.0.0.1:8010/health | python -m json.tool
```

For production-style approved Q&A testing:

```bash
APPROVED_QA_ENABLED=true \
APPROVED_QA_PATH=data/approved_qa/default.jsonl \
PYTHONPATH=. .venv/bin/uvicorn webapi.main:app \
  --host 127.0.0.1 \
  --port 8010
```

### Run with Docker

```bash
cp .env.example .env   # fill in real values; .env is never committed or baked into the image
docker compose up --build
```

The API listens on port 8000 with a `/health` healthcheck. Runtime data stays
on the host via bind mounts: `vectorstore/` (Chroma), `index/` (canonical
JSONL corpus), `data/` (approved Q&A), and `runs/` (audit/eval output). The
image itself contains only code; see `.dockerignore`.

---

## Multi-format input support

Word / Excel / CSV / PowerPoint / PDF documents can be converted into the
same canonical chunk JSONL used by the existing ingest/eval pipeline:

| Format | Parser | Chunk types |
|---|---|---|
| CSV | stdlib `csv` | `row`, `qa_pair` |
| XLSX | stdlib zip/XML (no openpyxl) | `row`, `qa_pair` (per sheet, with `sheet_name`/`row_number`/`cell_range`) |
| DOCX | stdlib zip/XML (no python-docx) | `paragraph` (with `heading_path`), `table_row`, `qa_pair` |
| PPTX | stdlib zip/XML (no python-pptx) | `slide` (with `slide_number`/`slide_title`), `table_row` |
| PDF | adapter around `scripts/pdf_to_canonical_jsonl.py` | existing PDF chunking (page metadata preserved) |

FAQ-style tables in CSV/XLSX/DOCX are detected by obvious column headers
(`question`/`answer`, `質問`/`回答`, `Q`/`A`, `問い合わせ`/`回答`, `質疑`/`応答`)
and emitted as Q+A pair chunks whose `searchable_text` contains both the
question and the answer.

```bash
PYTHONPATH=. .venv/bin/python scripts/convert_document_to_canonical_jsonl.py \
  --input docs_in/faq.xlsx --output index/faq.jsonl --tenant-id default

# also: --input manual.docx / deck.pptx / faq.csv / spec.pdf  (--format auto by extension)
```

Notes:

- Conversion only writes JSONL. **Nothing is ingested into the vectorstore
  automatically** — ingest stays the explicit `scripts/ingest_canonical_jsonl.py` step.
- OCR / image extraction is not included yet (image-only PDFs need the
  existing `--ocr` path of the PDF converter; pictures in Office files are skipped).
- XLSX values are cached cell values: formulas surface as their last computed
  result and date cells surface as raw serial numbers.

## Ingestion

Legacy fixed-window PDF chunking still works.
Japanese doc-type-aware chunking is available as an additive option.

Example:

```bash
PYTHONPATH=. .venv/bin/python scripts/pdf_to_canonical_jsonl.py \
  --pdf pdfs/your_doc.pdf \
  --out index/your_doc.jsonl \
  --doc-type procedure \
  --title "運用手順書" \
  --chunking ja_doc_type \
  --tenant-id default \
  --doc-version v1
```

Then ingest:

```bash
PYTHONPATH=. .venv/bin/python scripts/ingest_canonical_jsonl.py index/your_doc.jsonl --reset
```

### Chroma collection rebuild / alignment

When rebuilding the vectorstore, the Chroma collection should be built from the same canonical JSONL corpus used by the BM25 index.

Do not overwrite the current production collection directly. Create a candidate collection first, audit it, and promote it only after regression checks pass.

Build the normalized canonical JSONL:

```bash
PYTHONPATH=. .venv/bin/python tools/build_normalized_canonical_chunks.py \
  --input index/chunks.canonical.bytype.dedup.jsonl \
  --output index/chunks.canonical.normalized.jsonl \
  --report artifacts/corpus_alignment/metadata_normalization_report.md
```

For the current verified PDF corpus, the expected line count is:

```bash
wc -l index/chunks.canonical.normalized.jsonl
```

Expected:

```text
116
```

Create a new candidate Chroma collection:

```bash
PYTHONPATH=. .venv/bin/python scripts/ingest_canonical_jsonl.py \
  --input index/chunks.canonical.normalized.jsonl \
  --collection chatbot_chunks_v1_aligned_candidate
```

If the CLI options differ, check:

```bash
PYTHONPATH=. .venv/bin/python scripts/ingest_canonical_jsonl.py --help
```

Stamp the embedding fingerprint:

```bash
mkdir -p artifacts/manual_collection_rebuild

PYTHONPATH=. .venv/bin/python tools/stamp_chroma_collection_fingerprint.py \
  --collection chatbot_chunks_v1_aligned_candidate \
  --source-jsonl index/chunks.canonical.normalized.jsonl \
  --output artifacts/manual_collection_rebuild/embedding_fingerprint.json
```

Audit BM25 / Chroma corpus alignment:

```bash
PYTHONPATH=. .venv/bin/python tools/audit_corpus_alignment.py \
  --bm25-jsonl index/chunks.canonical.normalized.jsonl \
  --collection chatbot_chunks_v1_aligned_candidate \
  --output-dir artifacts/manual_collection_rebuild/alignment_audit
```

Evaluate the candidate collection:

```bash
PYTHONPATH=. .venv/bin/python tools/evaluate_normal_retrieval_vector_vs_hybrid.py \
  --cases artifacts/normal_retrieval_eval/normal_retrieval_cases.jsonl \
  --collection chatbot_chunks_v1_aligned_candidate \
  --output-dir artifacts/manual_collection_rebuild/normal_retrieval_candidate \
  --top-k 5
```

Promotion requires corpus alignment, fingerprint validation, live regression, and a rollback path.

### Table-style Q&A PDF ingestion

For PDFs that contain rows like:

```text
No / 質問項目 / 質問内容 / 回答
```

use the dedicated table converter instead of relying on generic FAQ chunking:

```bash
PYTHONPATH=. .venv/bin/python scripts/qanda_table_pdf_to_approved_qa.py \
  --pdf pdfs/58887_95105_misc.pdf \
  --out /tmp/tourism_qa_candidates.draft.jsonl \
  --source-doc 58887_95105_misc.pdf \
  --tenant-id default \
  --doc-version v1 \
  --status draft
```

Validate the draft output:

```bash
PYTHONPATH=. .venv/bin/python scripts/approved_qa_review.py validate \
  --in /tmp/tourism_qa_candidates.draft.jsonl
```

List draft records:

```bash
PYTHONPATH=. .venv/bin/python scripts/approved_qa_review.py list \
  --in /tmp/tourism_qa_candidates.draft.jsonl \
  --status draft \
  --limit 50
```

---

## Approved Q&A operation

Approved Q&A is the deterministic same-question answer layer.

### Convert CSV/JSON/JSONL to approved-QA JSONL

```bash
PYTHONPATH=. .venv/bin/python scripts/qa_to_approved_jsonl.py \
  --in input.csv \
  --out /tmp/approved_qa.generated.jsonl \
  --format csv \
  --tenant-id default \
  --status draft
```

### Review and promote records

List draft records:

```bash
PYTHONPATH=. .venv/bin/python scripts/approved_qa_review.py list \
  --in /tmp/approved_qa.generated.jsonl \
  --status draft \
  --limit 20
```

Promote one record:

```bash
PYTHONPATH=. .venv/bin/python scripts/approved_qa_review.py promote \
  --in /tmp/approved_qa.generated.jsonl \
  --out /tmp/approved_qa.reviewed.jsonl \
  --qa-id <qa_id> \
  --reviewer rai \
  --notes "checked"
```

Reject one record:

```bash
PYTHONPATH=. .venv/bin/python scripts/approved_qa_review.py reject \
  --in /tmp/approved_qa.reviewed.jsonl \
  --out /tmp/approved_qa.reviewed2.jsonl \
  --qa-id <qa_id> \
  --reviewer rai \
  --reason "bad answer"
```

Export approved records:

```bash
mkdir -p data/approved_qa

PYTHONPATH=. .venv/bin/python scripts/approved_qa_review.py export-approved \
  --in /tmp/approved_qa.reviewed2.jsonl \
  --out data/approved_qa/default.jsonl \
  --overwrite
```

### Verify approved-QA exact match

```bash
PYTHONPATH=. .venv/bin/python -m eval.approved_qa_runner \
  --cases data/approved_qa/default.jsonl \
  --output runs/eval/approved_qa_default.json
```

A production-ready approved-QA file must pass:

```text
failed=0
pass_rate=1.0
```

### Verify `/chat` exact match

```bash
APPROVED_QA_ENABLED=true \
APPROVED_QA_PATH=data/approved_qa/default.jsonl \
PYTHONPATH=. .venv/bin/uvicorn webapi.main:app \
  --host 127.0.0.1 \
  --port 8001
```

Then POST the same approved questions to `/chat` and verify:

- `answer_mode == approved_exact_match`
- `approved_qa_id` matches the approved record
- `answer_text` equals `approved_answer`
- `retrieved` is empty

---

## Evaluation

`eval.runner` supports two distinct positions:

1. deterministic local-friendly regression mode
2. retrieval-aware comparison mode

### Deterministic smoke evaluation

Default deterministic behavior:

- generation is stubbed
- vector retrieval is stubbed empty unless `--real-vector` is enabled
- keyword retrieval remains active

This mode is useful for regression checks, rerank movement checks, and guard/fallback consistency.

```bash
PYTHONPATH=. .venv/bin/python -m eval.runner \
  --cases eval/cases/smoke_cases.jsonl \
  --chunks-jsonl eval/cases/smoke_chunks.jsonl \
  --output runs/eval/smoke_results.json
```

### Retrieval-aware evaluation

Retrieval-aware evaluation compares baseline modes and saves:

- per-query rows as JSONL
- mode-level aggregate summary as JSON

Supported modes:

- `bm25_only`
- `dense_only`
- `hybrid`
- `hybrid_rerank`

```bash
PYTHONPATH=. .venv/bin/python -m eval.runner \
  --retrieval-aware \
  --cases eval/cases/retrieval_cases.jsonl \
  --chunks-jsonl eval/cases/smoke_chunks.jsonl \
  --modes bm25_only,dense_only,hybrid,hybrid_rerank \
  --per-query-output runs/eval/retrieval_rows.jsonl \
  --summary-output runs/eval/retrieval_summary.json \
  --eval-k 5
```

Per-query rows include signals such as:

- `gold_doc_hit`
- `gold_chunk_hit`
- `best_rank_before_rerank`
- `best_rank_after_rerank`
- `rerank_gain`
- `guard_reason`
- `used_fallback`
- `expected_abstain`
- `abstain_correct`

Mode-level summary includes:

- `gold_chunk_cases`
- `gold_chunk_hits`
- `gold_doc_cases`
- `gold_doc_hits`
- `abstain_labeled_cases`
- `abstain_expected_cases`
- `abstain_passes`
- `mean_mrr_at_k`
- `mean_ndcg_at_k`

Case sets:

- `eval/cases/smoke_cases.jsonl`: lightweight regression checks
- `eval/cases/retrieval_cases.jsonl`: labeled retrieval comparison cases

### Deterministic vs real-vector evaluation

Without `--real-vector`, vector retrieval is stubbed empty and dense-only results are not suitable for dense-quality conclusions.

Use `--real-vector` when evaluating actual vector contribution:

```bash
PYTHONPATH=. .venv/bin/python -m eval.runner \
  --retrieval-aware \
  --cases eval/cases/retrieval_cases.jsonl \
  --chunks-jsonl eval/cases/smoke_chunks.jsonl \
  --modes dense_only,hybrid,hybrid_rerank \
  --per-query-output runs/eval/retrieval_rows_real_vector.jsonl \
  --summary-output runs/eval/retrieval_summary_real_vector.json \
  --eval-k 5 \
  --real-vector
```

### Approved-QA regression

Approved Q&A files must be checked with:

```bash
PYTHONPATH=. .venv/bin/python -m eval.approved_qa_runner \
  --cases data/approved_qa/default.jsonl \
  --output runs/eval/approved_qa_default.json
```

Commercial acceptance criterion:

```text
pass_rate=1.0
failed=0
```

---

## Commercial data policy

Recommended policy:

- Do not commit private production PDFs by default.
- Do not commit sensitive production approved-QA files by default.
- Commit scripts, tests, examples, and safe sample data.
- Keep real customer data in controlled storage or deployment-specific paths.
- Every approved-QA deployment must run `approved_qa_runner`.
- Every update to `data/approved_qa/default.jsonl` should be traceable to a reviewed source.

For local development, `data/approved_qa/default.jsonl` may be used as the active approved-QA path.

---

## Reports

- [Retrieval Mode Evaluation Report](reports/retrieval_mode_report.md)
- [Deterministic vs Real-Vector Evaluation Notes](reports/real_vector_evaluation.md)
- [Commercial Roadmap](ROADMAP.md)

---

## Current limitations

- The retrieval comparison corpus is intentionally small and repo-native.
- Dense retrieval conclusions require `--real-vector`; deterministic mode is primarily for regression safety.
- Chunking and reranker settings are still an active tuning surface.
- Approved similar-match is not yet enabled for automatic answers.
- Table-style PDF extraction may require per-document column-boundary tuning.
- Production data governance should be handled outside the repository unless the data is safe to commit.

---

## License

See [LICENSE](LICENSE).
