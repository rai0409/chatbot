# Approved-QA source audit

## Executive conclusion

`data/approved_qa/default.jsonl` is safe as the current production approved-QA authority only for its 22-record tourism-PDF scope: every record is explicitly approved and has a unique QA ID, citation, tenant, and version; no normalized Q/A duplicate or answer conflict was found. It is not authority for the separate 96-record `040219e-biscfaq.pdf` corpus. No single governed source currently authorizes the combined 118-record corpus.

## Dataset inventory

| File | Classification | Records | Unique record IDs | Unique logical QA IDs | Source docs | approved / draft / rejected / missing | Missing citation / page / tenant / version / fingerprint |
| --- | --- | ---: | ---: | ---: | --- | --- | --- |
| `data/approved_qa/default.jsonl` | source-of-truth | 22 | 22 | 22 | 58887_95105_misc.pdf | 22 / 0 / 0 / 0 | 0 / 0 / 0 / 0 / 22 |
| `artifacts/fixed_qa_eval/ingest/040219_canonical_qa_pairs.jsonl` | derived-search-pair | 96 | 96 | 96 | 040219e-biscfaq.pdf | 0 / 0 / 0 / 96 | 0 / 0 / 0 / 0 / 96 |
| `artifacts/fixed_qa_eval/ingest/040219_approved_qa_ingest.jsonl` | derived-search-pair | 96 | 96 | 96 | 040219e-biscfaq.pdf | 96 / 0 / 0 / 0 | 0 / 0 / 0 / 0 / 96 |
| `artifacts/free_extractive_chat_mode/approved_qa_118_runtime.jsonl` | runtime-record | 118 | 118 | 118 | 040219e-biscfaq.pdf, 58887_95105_misc.pdf | 118 / 0 / 0 / 0 | 0 / 0 / 0 / 0 / 118 |
| `artifacts/fixed_qa_eval/fixed_qa_cases.jsonl` | evaluation-case | 118 | 118 | 0 | pdfs/040219e-biscfaq.pdf, pdfs/58887_95105_misc.pdf | 0 / 0 / 0 / 118 | 118 / 0 / 118 / 118 / 118 |
| `eval/cases/real_corpus_chunks.jsonl` | derived-search-pair | 30 | 30 | 22 | 58887_95105_misc.pdf | 0 / 0 / 0 / 30 | 8 / 0 / 0 / 0 / 30 |
| `eval/cases/real_corpus_cases.jsonl` | evaluation-case | 51 | 51 | 0 | — | 0 / 0 / 0 / 51 | 51 / 51 / 51 / 51 / 51 |

All non-empty lines parsed as JSON objects. No duplicate record IDs, duplicate normalized questions, duplicate normalized question-answer pairs, QA-ID answer conflicts, or normalized-question answer conflicts were found. `report.json` contains the full counters, SHA-256 values, and complete 96-entry lists.

## 22 vs 96 vs 118 explanation

- 22: tourism approved QA in `default.jsonl` (`58887_95105_misc.pdf`).
- 96: a distinct `040219e-biscfaq.pdf` QA set. Canonical and ingest files have identical QA-ID sets and normalized Q/A pairs; they are derived search-pair artifacts, not aliases of the 22.
- 118: the runtime artifact is exactly the union of the 22 and 96 QA IDs and normalized Q/A pairs: 96 are absent from `default.jsonl`, 22 are represented there.
- The 118 fixed-QA cases equal the runtime normalized Q/A pairs and are evaluation labels, not an independent authority.

## Conflicts and missing governance fields

- No content conflicts were found under the documented normalization.
- All audited records lack `source_fingerprint`/`source_jsonl_sha256`.
- The canonical 96 artifact has no explicit `status` (only `quality: approved`), which is insufficient as governed authority.
- Evaluation files lack authority metadata as quantified above.

## Recommended source of truth

Use `data/approved_qa/default.jsonl` for its current 22 records. Before authorizing the 96 or all 118 records, promote them into a governed `data/approved_qa` source or authoritative manifest with explicit status and provenance.

## Files excluded from production authority

- `artifacts/fixed_qa_eval/ingest/040219_canonical_qa_pairs.jsonl`
- `artifacts/fixed_qa_eval/ingest/040219_approved_qa_ingest.jsonl`
- `artifacts/free_extractive_chat_mode/approved_qa_118_runtime.jsonl`
- `artifacts/fixed_qa_eval/fixed_qa_cases.jsonl`
- `eval/cases/real_corpus_chunks.jsonl`
- `eval/cases/real_corpus_cases.jsonl`

These derived search-pair, runtime, and evaluation artifacts may be regenerated from authority but must never establish production content authority.

## Required deterministic transformation

1. Read only approved records from governed source files; reject missing or duplicate qa_id values.
2. Require non-empty approved question/answer/citations, tenant_id, doc_version, source document, and source pages.
3. Emit one qa_pair record per qa_id: id="approved_qa_pair:" + qa_id; approved_qa_id=qa_id; qa_id=qa_id; chunk_type="qa_pair"; type="approved_qa"; doc_type="approved_qa_pair"; searchable_text/text/display_text="Q: {question}\nA: {approved_answer}".
4. Copy citation, source document/page, tenant, version, review metadata, and deterministic source-file SHA-256; sort by qa_id.
5. Fail before ingestion on duplicate emitted id/qa_id or normalized-question/QA-ID answer conflicts.

## Blocking issues before production corpus build

- No audited record contains source_fingerprint or source_jsonl_sha256.
- The 96-record source exists only in artifacts; its canonical variant has no explicit status field (quality=approved only).
- No single governed source-of-truth file authorizes the 118-record corpus.
- Evaluation cases and runtime/search-pair files are derived outputs, not content authority.

## Audit method

Question/answer duplicate checks use Unicode-preserving casefolding and whitespace collapse. The JSON report records field-selection rules and all required per-file detail.
