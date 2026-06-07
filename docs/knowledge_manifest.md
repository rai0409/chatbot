# Knowledge Manifest

The knowledge manifest is a lightweight source inventory for commercial RAG / chatbot deployments. It records which approved QA files, PDFs, index JSONL files, and eval case files are present, versioned, checksummed, and active for a tenant.

The manifest is not wired into runtime retrieval yet. It is an operations artifact for review, deployment pinning, checksum drift detection, and future citation/source metadata hardening.

## Manifest Schema

Top-level fields:

- `manifest_version`: manifest schema version.
- `generated_at`: UTC timestamp when the manifest was generated.
- `records`: list of source records.
- `warnings`: bounded warnings from scanning or validation.

Source record fields:

- `source_id`: stable local identifier for the source.
- `tenant_id`: tenant/customer scope, default `default`.
- `source_type`: one of `pdf`, `approved_qa`, `index_jsonl`, `eval_case`, or `other`.
- `source_title`: human-readable source title, usually the filename.
- `source_path`: local relative path.
- `version`: source version string.
- `checksum`: SHA-256 checksum for local file records.
- `checksum_algorithm`: checksum algorithm, currently `sha256`.
- `status`: one of `active`, `deprecated`, or `archived`.
- `indexed_at`: optional timestamp for indexing.
- `updated_at`: optional timestamp for source update.
- `category`: optional grouping such as `approved_qa`, `pdf`, `index`, or `eval_case`.
- `metadata`: small safe metadata such as file size.

The manifest stores metadata and checksums only. It does not store file contents, private document text, chunks, approved answers, or audit payloads.

## Source Versioning

Use `source_id`, `version`, `checksum`, and `status` together:

- `source_id` identifies the logical source.
- `version` records the deployment-facing source version.
- `checksum` detects whether the local file bytes changed.
- `status` controls operational eligibility.

Recommended status semantics:

- `active`: eligible for indexing, evaluation, and citation.
- `deprecated`: retained for audit/history; do not answer from it unless explicitly allowed.
- `archived`: retained only for historical traceability.

Checksum mismatch means the document changed and should be reviewed, re-indexed, and re-evaluated before deployment.

## Build Command

Run:

```bash
.venv/bin/python eval/knowledge_manifest_builder.py
```

Default scan locations:

- `data/approved_qa/*.jsonl`
- `data/source_pdfs/*.pdf`
- `pdfs/*.pdf`
- `index/*.jsonl`
- `eval/cases/*.jsonl`

Default output:

```text
data/knowledge/manifest.json
```

The builder skips `__pycache__`, runtime logs, `runs/`, and `artifacts/eval/` outputs. Missing optional scan directories are reported as warnings rather than fatal errors.

## Production Guidance

- Review the manifest before deployment.
- Pin a manifest version per tenant/customer.
- Do not answer from deprecated or archived sources unless explicitly allowed by a reviewed policy.
- Use checksum mismatch detection to identify changed documents requiring re-indexing and evaluation.
- Use the manifest as the basis for future citation/source metadata hardening.
- Keep tenant source selection and runtime manifest enforcement separate until runtime integration is explicitly implemented.
