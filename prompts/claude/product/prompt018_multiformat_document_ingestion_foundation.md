# Prompt018: Multi-format Document Ingestion Foundation

You are working in:

/home/rai/chatbot

## Goal

Analyze the current repository and implement the first production-grade multi-format document ingestion foundation for the product direction:

Japanese private RAG chatbot for companies:
- Word / Excel / CSV / PowerPoint / PDF input
- citation-first answers
- approved-answer governance
- Q+A pair chunks
- tenant-secure private deployment

The immediate implementation target is:

Convert docx, xlsx, csv, pptx, and existing PDF-derived content into canonical JSONL chunks compatible with the current retrieval/eval/ingest pipeline.

This prompt should not build a UI and should not ingest into the production vectorstore. It should create a safe converter layer and tests.

## Execution mode

Proceed autonomously.

Do not ask for human confirmation for ordinary local edits, targeted tests, smoke checks, or local verification.

Stop only if one of the following occurs:

- A destructive operation would be required.
- User data would be deleted.
- .env, secrets, tokens, API keys, private credentials, or real customer data would need to be read, printed, changed, or inferred.
- A remote push, force push, remote deployment, or external service login would be required.
- Required target files cannot be found and the correct location is ambiguous.
- Required libraries are unavailable and no safe stdlib/minimal fallback exists.
- Verification fails in a way that cannot be safely classified after one bounded fix attempt.

If verification fails because of your changes, perform one bounded fix attempt, rerun targeted verification, and report final status.

## Preconditions to verify before implementing

Verify and report:

- Repo path and branch.
- git status summary.
- Prompt016 is complete:
  - tag prompt016-phase5c-eval-corpus-expansion exists, or commit evidence exists.
  - eval/cases/real_corpus_cases.jsonl exists.
  - runs/eval/prompt016_real_corpus_baseline.json exists.
- Prompt017 exists but do not execute it.
- Current canonical JSONL schema by inspecting existing scripts and fixtures:
  - scripts/approved_qa_to_canonical_jsonl.py
  - scripts/approved_qa_to_pair_chunks.py
  - scripts/ingest_canonical_jsonl.py
  - eval/cases/qa_pair_chunks.jsonl
  - eval/cases/real_corpus_chunks.jsonl
- Current PDF conversion path:
  - scripts/pdf_to_canonical_jsonl.py or equivalent existing PDF converter
- Current dependency files:
  - requirements.txt / pyproject.toml / setup.cfg if present
- Current document parsing libraries available locally:
  - openpyxl
  - python-docx / docx
  - python-pptx / pptx
  - pymupdf / fitz
  - standard csv module

Do not read or print .env.

## Product analysis requirement

Before implementation, write a short analysis section into the final report explaining:

1. What product this repo should become.
2. Why multi-format input is a product moat.
3. Which input formats should be supported first and why.
4. How docx/xlsx/csv/pptx/PDF should map to canonical chunks.
5. What should remain out of scope for this prompt.

The chosen product direction must be:

Japanese enterprise internal document AI answer bot:
- Word / Excel / PDF / PowerPoint / CSV input
- citation-first response
- approved Q&A deterministic route
- answerable/abstain guard
- tenant-secure private deployment

## Scope

Implement only the following.

### 1. Multi-format converter package

Create a small, localized converter package, for example:

rag_core/document_converters/

or an equivalent repo-consistent location.

It should support:

- CSV
- XLSX
- DOCX
- PPTX
- PDF via existing repo PDF converter integration or wrapper

Do not broad-refactor existing ingestion.

The package should expose a simple function or small API like:

convert_file_to_canonical_chunks(path, *, tenant_id="default", source_doc=None, doc_type=None) -> list[dict]

or an equivalent repo-consistent API.

### 2. Canonical chunk contract

All generated chunks must be compatible with the existing canonical JSONL ingest path.

Every chunk should include:

- id: deterministic stable id
- text: text for answer generation and display
- searchable_text: search text
- display_text: citation/display text when existing schema supports it
- metadata with at least:
  - tenant_id
  - source_doc
  - source_type: "csv" | "xlsx" | "docx" | "pptx" | "pdf"
  - doc_type
  - chunk_type
  - parser
  - stable location metadata depending on format

Format-specific metadata:

CSV:
- row_number
- columns
- sheet_name absent or null
- chunk_type="row" or "qa_pair" when Q/A columns are detected

XLSX:
- sheet_name
- row_number
- columns
- cell_range when useful
- chunk_type="row" or "qa_pair"
- detect FAQ/Q&A tables when obvious column names exist:
  - question / answer
  - 質問 / 回答
  - Q / A
  - 問い合わせ / 回答
  - 質疑 / 応答
- For Q&A rows, searchable_text must include both question and answer.

DOCX:
- heading_path when available
- paragraph_index or table_index
- chunk_type="section" | "paragraph" | "table_row" | "qa_pair"
- Preserve headings and tables as structured text.
- For tables with question/answer-like columns, emit qa_pair chunks.

PPTX:
- slide_number
- slide_title when available
- chunk_type="slide" or "table_row"
- Extract slide title, body text, and table text.
- Do not attempt image OCR in this prompt.

PDF:
- Reuse or wrap the existing PDF canonical converter.
- Preserve page metadata.
- Do not rewrite the existing PDF converter unless a small compatibility wrapper is necessary.
- If PDF conversion cannot be safely generalized, expose it as an adapter that calls the existing script/function and report limitations.

### 3. Converter CLI

Add a CLI script:

scripts/convert_document_to_canonical_jsonl.py

Required behavior:

- Input: one file path
- Output: one JSONL file path
- Options:
  - --tenant-id
  - --doc-type
  - --source-doc
  - --format auto|csv|xlsx|docx|pptx|pdf
- Auto-detect by extension when --format auto.
- Write deterministic JSONL.
- Print summary counts by source_type/chunk_type.
- Do not ingest into vectorstore.
- Do not modify source files.
- Do not read .env.

### 4. Tests

Add targeted tests with generated temporary files only.

Tests must cover:

CSV:
- row chunk generation
- Q/A column detection into qa_pair
- deterministic ids

XLSX:
- multiple sheets
- Q/A column detection
- tenant_id propagation
- sheet_name + row_number metadata

DOCX:
- headings and paragraph chunks
- table row extraction
- Q/A table detection if python-docx is available

PPTX:
- slide title/body extraction if python-pptx is available
- slide_number metadata

PDF:
- adapter/wrapper test using existing fixtures if safe
- or a small smoke asserting existing converter path is discoverable and not broken

Common:
- unsupported extension fails clearly
- converter CLI writes valid JSONL
- output loads through existing canonical JSONL expectations or eval runner fixtures where appropriate
- no new parser path imports optional libraries at module import time if the dependency may be missing
- tests must skip gracefully when optional parser packages are unavailable, unless the package is already required by the repo

### 5. Documentation

Update docs or README minimally.

Add a section:

Multi-format input support

Include:

- supported formats
- output canonical JSONL
- example commands for csv/xlsx/docx/pptx/pdf
- note that conversion does not automatically ingest into vectorstore
- note that OCR/image extraction is not included yet
- note that Excel/CSV FAQ tables can produce Q+A pair chunks

### 6. Next prompt

If PASS, write exactly one next prompt to:

prompts/claude/product/prompt019_multiformat_ingest_eval_and_onboarding.md

It should cover:

- running the new converters on sample docs
- building a multi-format eval corpus
- tenant onboarding dry-run
- import manifest
- duplicate detection
- one-command safe dry-run import
- no production vectorstore mutation by default

Do not execute Prompt019.

## Explicit non-goals

Do not implement:

- UI
- upload endpoint
- admin dashboard
- OCR for image PDFs
- SharePoint / Google Drive / OneDrive integration
- email ingestion
- zip/folder watcher
- production vectorstore ingestion
- changes to /chat behavior
- changes to guard thresholds
- Prompt017 calibration
- cross-encoder default changes
- tenant authorization changes
- new external services
- broad refactors

## Dependency policy

Prefer existing installed or already-declared packages.

Allowed if already present:
- openpyxl
- python-docx
- python-pptx
- pymupdf / fitz

If a parser package is unavailable:
- Do not add heavy dependencies without checking repo policy.
- Implement graceful optional import and targeted test skips.
- Report unavailable format support as PARTIAL only if the format cannot be implemented.

Do not require network access or model downloads.

## Verification

Run targeted tests first.

Then run:

python -m pytest --collect-only -q

Run existing deterministic smokes:

PYTHONPATH=. .venv/bin/python -m eval.runner \
  --cases eval/cases/smoke_cases.jsonl \
  --chunks-jsonl eval/cases/smoke_chunks.jsonl \
  --output runs/eval/prompt018_smoke_check.json

PYTHONPATH=. .venv/bin/python -m eval.runner \
  --cases eval/cases/qa_pair_cases.jsonl \
  --chunks-jsonl eval/cases/qa_pair_chunks.jsonl \
  --output runs/eval/prompt018_qa_pair_check.json

If safe, run:

scripts/product_readiness_smoke.sh

Run security/tenant tests if safe:

python -m pytest tests/test_api_key_tenant_authorization.py tests/test_tenant_isolation.py -q

Do not run full test suites unless targeted verification clearly requires it.

## Required final output

Report in this exact order:

1. Preconditions
   - repo path
   - branch
   - initial git status summary
   - Prompt016 evidence
   - Prompt017 unexecuted evidence
   - parser library availability
   - existing canonical/PDF ingest evidence

2. Product and technical direction analysis
   - confirmed product direction
   - why multi-format input is a moat
   - chosen implementation strategy
   - explicit non-goals

3. Implementation summary
   - files added/changed
   - converter API
   - CLI usage
   - exact canonical chunk schema
   - format support matrix
   - optional dependency behavior

4. Verification results
   - targeted tests
   - collect-only
   - smoke eval
   - qa_pair eval
   - product readiness smoke
   - security/tenant tests
   - skipped checks and why

5. Git diff summary
   - git diff --stat
   - no large unrelated diffs

6. Final judgment
   - PASS / PARTIAL / FAIL
   - whether it is safe to continue to Prompt019

7. Next prompt file
   - path to Prompt019
   - one-paragraph summary
