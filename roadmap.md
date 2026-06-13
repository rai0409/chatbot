# Commercial Chatbot Roadmap

This roadmap defines the commercial direction for the Japanese RAG chatbot.

The current system has already verified the most important deterministic route:

```text
approved Q&A exact match
same question -> same approved_answer
/chat API verification: 22/22 pass
```

The next goal is to expand from exact-match answers to similar-question search and robust RAG while preserving safety, citations, and reproducibility.

---

## Guiding principles

1. **Approved answers are the safest answers.**
   - If a question is already approved, return the approved answer exactly.
   - Do not rewrite approved answers with an LLM.

2. **Similarity is not the same as exact match.**
   - Similar-question matching must be introduced first as debug/candidate search.
   - Automatic similar-answer routing requires strict thresholds and evaluation.

3. **RAG must retrieve complete evidence.**
   - For Q&A documents, Q and A must be indexed together as a pair.
   - PDF-derived chunks alone can split questions and answers.

4. **Everything must be inspectable.**
   - Return metadata for answer mode, approved-QA ID, retrieval candidates, score details, and citations.

5. **Every production data update must be testable.**
   - Approved-QA updates must pass `approved_qa_runner`.
   - RAG updates must pass smoke and retrieval debug checks.

---

## Current verified state

### Completed

- Japanese doc-type-aware PDF chunking
- Chroma ingest metadata sanitation
- `/search/debug` endpoint
- retrieval-only debug mode
- query type and keyword score details
- conservative keyword boost for exact/identifier queries
- approved Q&A exact-match framework
- approved Q&A intake converter
- contract-ingest JSON to canonical JSONL converter
- canonical JSONL to draft approved-QA candidate converter
- approved-QA review/promote/export CLI
- table-style Q&A PDF to approved-QA converter

### Real-data verification

Using `58887_95105_misc.pdf`:

- PDF table extraction: 22 Q&A records
- approved-QA runner: 22/22 pass
- `/chat` production path: 22/22 exact same-question approved_answer match
- canonical vector ingest: 104 records ingested, skipped=0

### Commercial meaning

The system can already support a safe production mode for known official Q&A:

```text
user question
-> exact approved-QA match
-> approved_answer
-> approved citations
```

This is suitable for official FAQ, public Q&A, bid-question answers, and internal support templates.

---

## Target answer routing

The commercial `/chat` route should use this order:

```text
1. approved_exact_match
2. approved_similar_candidate / approved_similar_match
3. qa_pair_rag
4. normal_document_rag
5. fallback
```

### 1. approved_exact_match

Status: **implemented and verified**

Behavior:

```text
normalized user question == approved normalized_question
-> return approved_answer exactly
```

Acceptance:

- `answer_mode=approved_exact_match`
- `approved_qa_id` present
- `answer_text == approved_answer`
- no retrieval required
- no LLM required
- `approved_qa_runner` pass_rate=1.0

### 2. approved_similar_candidate

Status: **next major capability**

Behavior:

```text
no exact match
-> search approved Q&A questions
-> return candidate approved records with scores
```

Initial release should be debug-only.
It should not auto-answer.

Required metadata:

- candidate `qa_id`
- candidate question
- approved answer preview
- semantic score
- keyword score
- top1/top2 margin
- matched terms
- matched fields
- source citation

### 3. approved_similar_match

Status: **future, after candidate evaluation**

Behavior:

```text
no exact match
-> high-confidence approved-QA similar match
-> return approved_answer
```

Commercial gate:

- high score threshold
- adequate top1/top2 margin
- keyword evidence
- no conflicting close candidate
- regression cases pass

Suggested initial threshold policy:

```text
top_score >= 0.92
top1_top2_margin >= 0.08
keyword_score >= 0.50
```

These values must be evaluated before production use.

### 4. qa_pair_rag

Status: **recommended next PR**

Behavior:

```text
approved Q&A JSONL
-> canonical Q+A pair chunks
-> vectorstore
-> RAG can retrieve Q and A together
```

This addresses the known failure where PDF chunking can separate question and answer chunks.

### 5. normal_document_rag

Status: **implemented**

Used for:

- procedures
- manuals
- policies
- specs
- general documents
- non-approved questions

### 6. fallback

Status: **implemented**

Used when evidence is weak or the question is too broad.

---

## Roadmap phases

## Phase 1: Deterministic approved-answer layer

Status: **complete**

Delivered:

- approved Q&A JSONL schema
- exact-match normalization
- approved-QA loader and validator
- `/chat` exact-match integration
- approved-QA runner
- CSV/JSON/JSONL intake
- review/promote/reject/export CLI
- table-style Q&A PDF converter
- production path verification

Commercial acceptance:

```text
/chat exact same-question approved_answer: 22/22 pass
```

---

## Phase 2: Q+A pair RAG foundation

Status: **next**

Goal:

Convert approved Q&A records into canonical Q+A pair chunks for vector retrieval.

Why:

- table-style PDF chunks can split Q and A
- RAG should retrieve a complete Q+A pair
- this improves similar and partial question retrieval

Deliverables:

- `scripts/approved_qa_to_canonical_jsonl.py`
- tests for approved-QA to canonical conversion
- ingest verification
- `/search/debug` verification that Q+A pair chunks are retrievable

Acceptance:

```text
approved_qa/default.jsonl -> 22 canonical qa_pair rows
ingest: skipped=0
/search/debug retrieves qa_pair chunks for related questions
smoke eval remains 21/21
full pytest passes
```

---

## Phase 3: Approved similar-question search debug

Status: **planned**

Goal:

Search approved Q&A questions for similar questions without auto-answering.

Deliverables:

- approved-QA question search index
- debug CLI or API endpoint
- top-k candidate output
- score details
- keyword evidence
- margin calculation
- evaluation cases for paraphrases

Example:

```text
query: 15問に自由回答は入りますか？
candidate: 15問程度の項目はフリーアンサーも含まれるという認識で良いでしょうか。
answer: フリーアンサーも含みます。
```

Acceptance:

```text
paraphrase cases retrieve correct approved qa_id at top1
no auto-answer yet
candidate output is inspectable
```

---

## Phase 4: High-confidence approved similar match

Status: **planned**

Goal:

Allow automatic approved answers for high-confidence similar questions.

Deliverables:

- threshold config
- margin config
- conflict detection
- `answer_mode=approved_similar_match`
- audit logs
- negative test cases

Acceptance:

```text
high-confidence paraphrases pass
confusable cases do not auto-answer
unknown questions fall back to RAG
approved exact match remains 100%
```

---

## Phase 5: Feedback loop and ranking improvement

Status: **planned**

Goal:

Use operator/user feedback to improve retrieval ranking and approved-QA coverage.

Deliverables:

- feedback endpoint or CLI
- feedback JSONL event log
- rating schema
- reason labels
- bad-answer capture
- suggested approved-QA candidates
- retraining or score adjustment hooks

Feedback schema should include:

- request_id
- trace_id
- user question
- answer_mode
- selected qa_id or retrieved chunks
- rating
- reason
- corrected answer if available
- reviewer
- timestamp

Acceptance:

```text
feedback events are bounded
no private data leakage in logs
feedback can be tied back to retrieval traces
```

---

## Phase 6: Production deployment policy

Status: **planned**

Goal:

Make production data updates safe and repeatable.

Deliverables:

- deployment runbook
- data policy
- rollback process
- approved-QA release checklist
- vectorstore rebuild checklist
- monitoring fields

Acceptance before deployment:

```text
approved_qa_runner pass_rate=1.0
/chat approved exact sample pass
/search/debug pass
non-match fallback pass
smoke eval pass
full pytest pass
```

---

## Commercial operating procedure

### For table-style Q&A PDFs

Use this path:

```text
PDF
-> qanda_table_pdf_to_approved_qa.py
-> draft approved-QA JSONL
-> review/promote/export
-> data/approved_qa/default.jsonl
-> approved_qa_runner
-> /chat exact-match verification
-> approved_qa_to_canonical_jsonl.py
-> vectorstore ingest
```

### For normal documents

Use this path:

```text
PDF
-> pdf_to_canonical_jsonl.py
-> ingest_canonical_jsonl.py
-> /search/debug
-> /chat grounded RAG
```

### For contract-ingest JSON

Use this path:

```text
contract-ingest JSON
-> contract_ingest_json_to_canonical_jsonl.py
-> ingest_canonical_jsonl.py
-> RAG
```

If the extracted content is Q&A-like, convert it to draft approved-QA and review it.

---

## Production quality gates

### Approved Q&A gates

Required:

```text
approved_qa_runner pass_rate=1.0
failed=0
```

Required `/chat` checks:

```text
exact same question -> approved_exact_match
answer_text == approved_answer
approved_qa_id matches
non-approved question does not return approved_exact_match
```

### RAG gates

Required:

```text
/search/debug returns relevant evidence
citations exist
guard/fallback behavior is explicit
smoke eval passes
```

### Data gates

Required:

```text
no unreviewed draft records in production approved-QA file
no rejected records in production approved-QA file
tenant_id is correct
source_doc/source_pages exist
doc_version is present
```

---

## Data policy

Recommended commercial policy:

- Keep real production PDFs out of git unless explicitly safe.
- Keep customer-specific approved-QA files out of git by default.
- Commit scripts, tests, and safe examples.
- Store production approved-QA files in controlled deployment storage.
- Every approved-QA file must be reproducible from source or reviewed export.
- Every release must record:
  - source document
  - extraction command
  - review command
  - approved-QA runner output
  - API exact-match verification output

---

## Immediate next PR

Recommended next PR:

```text
PR5: Convert approved Q&A to canonical Q+A pair chunks for RAG
```

Why this is next:

- exact match is already complete
- approved Q&A has high-quality Q+A pairs
- RAG needs Q and A together
- this helps future similar-question search
- it does not risk changing approved exact-match behavior

---

## Success definition

The commercial chatbot is considered ready for a first internal pilot when:

```text
1. approved exact match works for all approved Q&A
2. Q+A pair chunks are searchable by RAG
3. non-approved questions fall back safely
4. /search/debug exposes why a result was selected
5. smoke eval and approved-QA regression pass
6. operators can add/review/export approved Q&A without code changes
```
