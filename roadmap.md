# Commercial Japanese RAG / Chatbot Roadmap

## Purpose

This roadmap defines the commercial-product direction for the Japanese RAG chatbot.

The product must prioritize:

* deterministic approved answers
* grounded extractive answers
* explicit refusal when evidence is insufficient
* inspectable retrieval and answer traces
* safe QA and tuning updates
* measurable improvement and regression detection
* non-engineer operation
* staging-first data changes
* controlled promotion and rollback

The objective is not merely to answer questions.

The objective is to make every answer, QA update, tuning change, document update, and production promotion measurable, reviewable, and reversible.

---

## Commercial product principles

### 1. Approved answers remain deterministic

When an approved QA exact match is found:

* return the approved answer without LLM rewriting
* include approved QA ID
* include approved citations
* record the answer mode
* preserve tenant isolation

### 2. Similarity must not be treated as certainty

Similar-question retrieval must first operate as:

* candidate retrieval
* debug output
* score inspection
* conflict inspection
* regression evaluation

Automatic approved similar-answer routing must not be enabled until commercial gates are satisfied.

### 3. Unknown questions must be refused safely

When the available evidence does not answer the requested fact:

* do not produce a plausible unsupported answer
* distinguish related evidence from sufficient evidence
* return an explicit fallback or abstention
* record the guard reason

### 4. Retrieval units must contain usable evidence

For approved QA:

* the question and answer must remain together
* source document and page metadata must be preserved
* canonical QA-pair chunks must remain traceable to the approved QA record

For normal documents:

* headings, sections, pages, tables, and document identity must be preserved where available

### 5. Every update must be evaluated before promotion

The following must use candidate-first workflows:

* approved QA additions
* aliases
* stopwords
* keep words
* synonyms
* domain terms
* retrieval weighting
* chunking
* reranking
* document ingestion
* vectorstore rebuilds

No candidate may silently overwrite the production baseline.

### 6. Non-engineers must be able to operate the product safely

A non-engineer should eventually be able to:

* create QA records in Excel
* select arbitrary Excel file and column names
* run dry-run validation
* see invalid rows
* see whether quality improved or worsened
* reject a bad candidate
* approve a passing candidate
* test a staging collection
* roll back a promoted version

---

# Current verified state

## Git and CI

Verified:

* GitHub default branch: `main`
* local and GitHub `main` were synchronized after PR #18
* GitHub Actions push check passed
* GitHub Actions pull-request check passed
* product readiness smoke passed:

  * 117 passed
  * 1 Authlib deprecation warning

The Authlib warning is not currently a test failure but remains technical debt.

## Approved QA exact-answer route

Status: **implemented and verified**

Capabilities:

* approved QA schema
* approved QA loader and validation
* exact-question normalization
* deterministic approved answer response
* approved answer citations
* approved QA ID
* approved QA evaluation runner
* non-approved questions do not use exact-match mode
* review/promote/reject/export workflow
* CSV/JSON/JSONL intake
* Q&A-style PDF conversion

Verified quality:

* exact QA: 118/118
* answer match rate: 1.0

Earlier real-data verification:

* table-style PDF extraction: 22 QA records
* `/chat` exact approved-answer path: 22/22

Commercial meaning:

Registered approved QA questions can already return their approved answers correctly.

The next QA-related problem is not creating another answer route.

The next problem is making QA additions safe and usable for non-engineers.

## Grounded extractive answer mode

Status: **implemented and verified**

Capabilities:

* free local extractive answer generation
* no OpenAI API required for extractive mode
* citation-preserving answer output
* answer quality gate
* required-term validation
* source/page validation
* unsupported-answer detection

Verified quality:

* grounded extractive quality: 14/14
* unsupported answers: 0
* failed checks: none

## Unknown abstention

Status: **implemented and verified**

Capabilities:

* evidence sufficiency checks
* distinction between related evidence and answer evidence
* fallback when a requested fact is absent
* generalized answer-type/evidence guards

Verified quality:

* unknown abstention: 32/32

Commercial meaning:

The current extractive mode is designed to avoid answering unsupported questions merely because related evidence exists.

## Normal retrieval

Status: **implemented and verified for the current evaluation set**

Capabilities:

* Chroma vector retrieval
* keyword/BM25-style retrieval support
* hybrid retrieval
* retrieval debug output
* query type metadata
* keyword evidence
* conservative keyword boost
* canonical metadata
* source/page metadata
* tenant-aware retrieval paths

Verified quality:

* hybrid_hit@5: 1.0

This is a current-dataset baseline, not proof of universal retrieval accuracy.

## Approved QA pair RAG

Status: **implemented**

Capabilities:

* approved QA JSONL to canonical Q+A pair conversion
* one approved QA record becomes one self-contained retrieval unit
* question and approved answer remain together
* source document and page metadata are preserved
* tenant, document version, review metadata, and QA ID are preserved
* non-approved draft records are excluded
* existing exact-match evaluation remains independent

Existing implementation:

* `scripts/approved_qa_to_canonical_jsonl.py`
* `tests/test_approved_qa_to_canonical_jsonl.py`

Remaining work:

* larger real-data retrieval evaluation
* paraphrase retrieval test set
* candidate ranking leaderboard
* operator-visible QA-pair retrieval diagnostics

## Metadata and reproducibility

Status: **partially implemented**

Capabilities include:

* canonical metadata handling
* document and chunk identity
* fingerprint audit tooling
* current baseline artifacts
* validation reports
* controlled production switch planning

Remaining work:

* clean-runner reproducibility gate
* environment-independent artifact paths
* baseline artifact versioning in Git
* dataset and vectorstore manifest
* dependency lock validation

## Promotion safety

Status: **planned and partially documented**

Current direction:

* candidate-first changes
* no direct production overwrite
* controlled production switch plan
* promotion decision report
* rollback requirement

Remaining work:

* executable promotion command
* immutable release manifest
* approved candidate identifier
* operator approval audit
* rollback command
* production pointer switch
* promotion integration test

---

# Correct commercial answer-routing target

The intended route is:

1. approved_exact_match
2. approved_similar_candidate
3. approved_similar_match
4. qa_pair_rag
5. normal_document_rag
6. fallback

## approved_exact_match

Status: **complete**

Requirements:

* exact or approved normalized match
* approved answer returned without rewriting
* QA ID included
* citations included
* no LLM dependency

## approved_similar_candidate

Status: **not yet commercially complete**

Purpose:

* retrieve likely approved QA candidates
* do not auto-answer
* expose candidate evidence for evaluation

Required output:

* QA ID
* approved question
* answer preview
* semantic score
* keyword score
* normalized overlap
* matched terms
* top1/top2 margin
* source citation
* conflict indicator

## approved_similar_match

Status: **not enabled as a commercial route**

Must not use unvalidated fixed thresholds.

Thresholds must be learned from an evaluation set containing:

* safe paraphrases
* close but different questions
* contradictory questions
* insufficient-evidence questions
* numeric/date/entity confusions
* tenant conflicts

## qa_pair_rag

Status: **implementation complete; commercial evaluation incomplete**

The Q+A pair converter exists.

The next work is not to recreate it.

The next work is to evaluate and integrate it into measurable candidate retrieval and non-engineer QA operations.

## normal_document_rag

Status: **implemented; input workflow incomplete**

Works for the current indexed document pipeline.

Remaining gaps:

* browser-based document upload
* extraction preview
* staging collection
* format-specific validation
* operator approval

## fallback

Status: **implemented and verified for current unknown tests**

Remaining work:

* broader adversarial unknown set
* numeric/date/entity mismatch cases
* production monitoring
* false-refusal analysis

---

# Execution roadmap

## Prompt 1: Commercial baseline snapshot

Status: **implemented locally**

Delivered locally:

* `docs/commercial_rag_quality_baseline.md`
* `artifacts/commercial_quality/current_baseline.json`
* `artifacts/commercial_quality/baseline_summary.md`

Verified:

* grounded quality 14/14
* unknown abstention 32/32
* exact QA 118/118
* hybrid_hit@5 1.0
* product readiness smoke 117 passed

Remaining action:

* review
* commit the baseline document and selected safe artifacts
* push through normal branch/PR workflow

## Prompt 2: Existing QA operator workflow audit and Excel gap implementation

Status: **next**

Purpose:

Determine exactly what already exists for QA import and operator workflows, then implement only the missing commercial gaps.

Must inspect:

* approved QA storage format
* CSV/JSON/JSONL intake
* table-style PDF intake
* review/promote/reject/export CLI
* exact normalization
* aliases
* existing Excel support
* existing column mapping
* existing validation reports
* existing source/page verification
* existing candidate and production separation

Implement only missing features, likely including:

* `.xlsx` QA management input
* arbitrary file names
* arbitrary column mapping
* Japanese column aliases
* dry-run
* invalid-row report
* candidate-only output
* deterministic QA ID
* conflict detection against existing approved QA
* no production apply

## Prompt 3: Approved QA 100% regression and alias contract

Purpose:

Maintain 100% correctness for registered approved questions while adding controlled aliases.

Scope:

* approved exact question
* normalized approved question
* explicitly approved aliases

Must not claim 100% accuracy for unrestricted free-form questions.

Required gates:

* existing exact QA remains 100%
* alias test set remains 100%
* conflicting aliases fail validation
* unknown abstention does not regress
* grounded quality does not regress

## Prompt 4: QA candidate evaluation and promotion gate

Purpose:

Evaluate an imported QA candidate before it can be approved.

Checks:

* new QA answer correctness
* existing QA regression
* alias collision
* source/page existence
* required-term evidence
* unknown abstention
* grounded quality
* retrieval effect

Outputs:

* pass/fail summary
* row-level errors
* regression list
* promotion recommendation

No automatic production promotion.

## Prompt 5: Approved similar-candidate retrieval

Purpose:

Add inspectable similar-question retrieval without automatic answering.

Deliverables:

* debug CLI or endpoint
* candidate leaderboard
* semantic and keyword scores
* margin
* conflict indicator
* source citations
* paraphrase evaluation set

The answer route remains unchanged.

## Prompt 6: Similar-question safety evaluation

Purpose:

Determine whether automatic approved-similar routing can ever be enabled safely.

Evaluation classes:

* valid paraphrases
* same words, different meaning
* numeric differences
* year/date differences
* entity differences
* negation
* conflicting approved QA
* unknown questions

Outputs:

* threshold curves
* precision
* recall
* false-positive list
* false-negative list
* recommended operating point

Commercial requirement:

False approved-answer routing must be treated as more severe than fallback.

## Prompt 7: RAG tuning data contract

Purpose:

Allow non-engineers to manage tuning values through Excel or CSV.

Supported candidate types:

* stopwords
* keep words
* synonyms
* domain terms
* phrase terms
* negative terms
* token normalization exceptions
* keyword boosts

Requirements:

* arbitrary file name
* arbitrary column mapping
* tenant/collection scope
* candidate-only output
* schema validation
* duplicate/conflict detection

## Prompt 8: Tuning A/B evaluation

Purpose:

Show whether a tuning candidate improved or worsened quality.

Metrics:

* exact QA accuracy
* alias QA accuracy
* grounded answer pass rate
* unknown abstention
* recall@1
* recall@5
* recall@20
* MRR
* fallback rate
* unsupported-answer count
* wrong-approved-answer count

The report must clearly classify:

* improved
* neutral
* mixed
* regressed
* unsafe

A candidate that improves recall but worsens approved-answer safety must fail.

## Prompt 9: Chunking and preprocessing candidate framework

Purpose:

Evaluate preprocessing changes without damaging the baseline.

Candidate variables:

* Unicode normalization
* Japanese punctuation
* MeCab/tokenizer behavior
* stopwords
* keep words
* chunk size
* overlap
* heading inheritance
* table row grouping
* Q+A pair grouping
* metadata propagation

Requirements:

* chunk lineage
* baseline/candidate separation
* deterministic build
* no production overwrite
* A/B metrics

## Prompt 10: Retrieval pipeline leaderboard

Purpose:

Compare retrieval architectures on the same fixed evaluation set.

Candidates:

* keyword only
* vector only
* hybrid
* hybrid plus rerank
* metadata-filtered hybrid
* QA-pair-specific retrieval
* normal-document retrieval

Evaluation must include both relevance and answer safety.

## Prompt 11: QA management UI

Purpose:

Allow a non-engineer to:

* upload QA Excel
* map columns
* run dry-run
* inspect invalid rows
* inspect conflicts
* download reports
* submit a candidate for approval

No direct production write.

## Prompt 12: Tuning management UI

Purpose:

Allow a non-engineer to:

* upload tuning Excel or CSV
* inspect normalized candidate values
* run A/B evaluation
* see improved/regressed metrics
* approve or reject a candidate

Unsafe candidates must not be promotable.

## Prompt 13: Raw document upload and staging ingestion

Purpose:

Add a browser-based document workflow.

Initial priority:

* PDF

Later formats, only after dedicated gates:

* DOCX
* CSV
* XLSX
* PPTX
* PNG/JPEG OCR

Workflow:

upload
→ security checks
→ extraction preview
→ canonical normalization
→ candidate chunks
→ staging collection
→ staging retrieval test
→ staging chat test

## Prompt 14: Staging chat UI

Purpose:

Allow operators to select and test a staging collection.

Must show:

* active collection
* production/staging status
* dataset fingerprint
* document count
* chunk count
* retrieval trace
* citations
* answer mode

## Prompt 15: Controlled promotion and rollback

Purpose:

Promote only validated QA, tuning, chunking, or vectorstore candidates.

Requirements:

* immutable candidate ID
* baseline ID
* validation report
* operator identity
* approval reason
* production pointer switch
* rollback pointer
* audit event
* integration test

## Prompt 16: Observability and quality monitoring

Purpose:

Measure commercial behavior after deployment.

Monitor:

* answer modes
* fallback rate
* unsupported-answer guard
* approved exact usage
* similar-candidate usage
* retrieval misses
* citation absence
* latency
* cache hit rate
* tenant isolation errors
* operator changes

Do not store unnecessary sensitive question contents.

## Prompt 17: Security and tenant isolation hardening

Purpose:

Validate commercial security boundaries.

Scope:

* API authentication
* admin authentication
* tenant isolation
* staging collection authorization
* upload limits
* formula injection
* path traversal
* malicious files
* audit integrity
* secrets handling
* dependency vulnerabilities

## Prompt 18: LLM mode quality gate

Purpose:

Evaluate LLM mode separately from extractive mode.

Must measure:

* faithfulness
* citation correctness
* unsupported claims
* instruction-following
* refusal behavior
* prompt injection resistance
* cost
* latency

LLM mode must not inherit the extractive mode quality claim automatically.

## Prompt 19: Format-specific ingestion gates

Purpose:

Validate each document format independently.

Separate gates:

* PDF
* DOCX
* CSV
* XLSX
* PPTX
* image/OCR

A format must not be advertised as commercially supported until its dedicated gate passes.

## Prompt 20: Clean-runner and reproducible release gate

Purpose:

Prove that a clean environment can reproduce:

* dependencies
* canonical corpus
* embeddings
* vectorstore
* QA baseline
* evaluation artifacts
* CI results

Deliverables:

* dependency lock
* dataset manifest
* model manifest
* vectorstore fingerprint
* build command
* release manifest
* verification command

---

# Commercial quality gates

## Required before every QA release

* approved exact QA accuracy remains 100%
* alias QA accuracy remains 100% where aliases are approved
* unknown abstention does not regress
* grounded quality does not regress
* source and page references are valid
* no duplicate or conflicting QA
* no unreviewed records enter production
* tenant IDs are valid

## Required before every tuning release

* baseline and candidate are both evaluated
* exact QA does not regress
* wrong-approved-answer count remains zero
* unknown abstention does not regress
* grounded quality does not regress
* retrieval metrics are reported
* regressions are listed individually

## Required before every vectorstore release

* dataset manifest exists
* chunk count is recorded
* skipped count is recorded
* metadata schema passes
* fingerprint is recorded
* staging retrieval passes
* rollback target exists

## Required before claiming commercial support

* feature has a dedicated test
* feature has a dedicated evaluation artifact
* feature is reproducible
* failure behavior is documented
* operational workflow exists
* rollback exists where production data is affected

---

# Immediate next work

The next task is not to recreate approved QA answering or Q+A pair conversion.

The next task is:

**Audit the existing QA operator workflow and implement only the missing Excel-based candidate intake capabilities.**

This is selected because:

* approved exact answers already work
* QA-pair canonical conversion already exists
* CSV/JSON/JSONL and review workflows already exist
* non-engineer Excel operation remains the main usability gap
* candidate validation is required before UI implementation
* this work does not need to change the current answer route
