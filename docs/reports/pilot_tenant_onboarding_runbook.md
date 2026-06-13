# Pilot Tenant Onboarding Runbook

How to onboard a single pilot tenant for the limited external beta, safely and
repeatably. Dry-run first; ingest only into an explicit non-production /
pilot collection; never touch the production/default collection.

**No real customer data.** Use synthetic or sanitized documents for the beta.
**No raw API keys** in any command, log, or example — placeholders only.

Throughout, `<tenant>` is the pilot tenant id (an allowlisted id, e.g.
`pilot_tenant_a`), and `<pilot_collection>` is an explicit non-production
collection name (e.g. `pilot_<tenant>_v1`).

## 1. Collect synthetic or sanitized pilot documents

- [ ] Gather the pilot corpus into a per-tenant input directory
      `<sanitized_docs_dir>` (supported formats: PDF/DOCX/PPTX/CSV/MD/TXT/…).
- [ ] Confirm the documents are synthetic or sanitized — no real customer
      PII or confidential third-party content. The beta runs on safe data.

## 2. Run the dry-run onboarding (no ingest)

```bash
.venv/bin/python scripts/onboard_documents_dry_run.py \
  --input-dir <sanitized_docs_dir> \
  --tenant-id <tenant>
```

- Dry-run by default: it converts → builds an import manifest → validates →
  prints exactly what *would* be ingested. It does **not** write to any
  vectorstore unless `--execute` **and** a non-production `--collection` are
  both given, and it refuses the production/default collection even then.
- [ ] Dry-run completes and prints the planned import.

## 3. Review the onboarding manifest

The run writes `runs/onboarding/<tenant>/manifest.json`.

```bash
.venv/bin/python -c "import json,sys; m=json.load(open(sys.argv[1])); \
print('ok=',m['ok']); print('issues=',{k:v for k,v in m['issues'].items() if v})" \
runs/onboarding/<tenant>/manifest.json
```

- [ ] `manifest["ok"]` is `true`.

### 3a. Duplicate IDs and duplicate text

Inspect `manifest["issues"]`:

- [ ] `duplicate_ids` is empty (no chunk id appears twice).
- [ ] `duplicate_texts` is empty (no identical chunk text under different
      ids). `informational.parent_child_duplicates` is expected/benign
      (parent-child expansion) and does not block.

### 3b. Tenant mismatch

- [ ] `tenant_mismatches` is empty (a single `source_doc` does not carry
      conflicting tenant ids).
- [ ] `unexpected_tenants` is empty (no tenant id other than `<tenant>` when
      an expected tenant was set). If a separate canonical import manifest is
      built, pass `--expected-tenant <tenant>`:

```bash
.venv/bin/python scripts/import_manifest.py \
  --inputs <canonical_jsonl>... \
  --output runs/onboarding/<tenant>/import_manifest.json \
  --expected-tenant <tenant> \
  --existing <previous_manifest.json>    # optional: collision check vs prior imports
```

- [ ] `collisions` is empty (no `source_doc`/chunk-id overlap with an earlier
      import).

## 4. Approve the knowledge manifest

- [ ] Generate/refresh the knowledge manifest for the pilot corpus and review
      it against `docs/knowledge_manifest.md` (source coverage, versions).
- [ ] A named reviewer signs off on the manifest before ingest.

## 5. Ingest into an explicit non-production / pilot collection

Only after §3–§4 are clean and approved:

```bash
.venv/bin/python scripts/onboard_documents_dry_run.py \
  --input-dir <sanitized_docs_dir> \
  --tenant-id <tenant> \
  --collection <pilot_collection> \
  --execute
```

- `--execute` requires an explicit non-production `--collection`; the tool
  refuses the production/default collection (`CHROMA_COLLECTION` /
  `VECTORSTORE_COLLECTION_NAME`) even with `--execute`.
- [ ] Ingest targets `<pilot_collection>` (non-production), not the default.
- [ ] Production/default vectorstore is untouched.

## 6. Verify with a small approved-QA smoke

- [ ] Add a few synthetic approved Q&A pairs for `<tenant>` and confirm a
      deterministic exact-match answer end to end against the pilot
      collection (the deploy smoke exercises this path with synthetic data):

```bash
bash scripts/deploy_smoke.sh
```

- [ ] A representative pilot query returns a grounded or approved answer with
      citations scoped to `<tenant>`; an out-of-scope query abstains
      (`too_general` / no-answer) rather than guessing.

## 7. Document pilot scope and exit criteria

Record per pilot tenant (keep with the launch checklist sign-off):

- [ ] **Scope**: tenant id, corpus description, allowed query topics, pilot
      key id (placeholder/reference only — never the raw key), collection
      name, start/end dates.
- [ ] **Success criteria**: e.g. answer/abstain rates within target, no
      cross-tenant exposure, acceptable latency, pilot satisfaction.
- [ ] **Exit criteria**: conditions to graduate (promote beyond limited beta —
      see `beta_go_no_go_assessment.md` re-evaluation triggers) or to
      offboard (stop traffic, remove key from `API_AUTH_KEYS` /
      `API_AUTH_TENANT_MAP`, delete the pilot collection and the tenant's
      audit data per the data agreement).

## Notes

- Onboarding is additive and reversible: offboarding a pilot tenant means
  removing its key mapping, dropping its non-production collection, and
  applying retention/deletion to its audit logs (`docs/operations.md`).
- This runbook never enables similar auto-answer, LLM answer/rerank, or debug
  comparison — the beta serves via `production_safe`.
