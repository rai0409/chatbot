# Prompt056: Safe Collection Promotion Workflow

Implementation report. Adds an operator-invoked, default-off **planning/approval**
workflow to gate promotion of a reviewed NON-production staging set to an explicit
served collection — without itself mutating any vectorstore.

## Implementation summary

- `webapi/collection_promotion.py` (new) — `plan_promotion(inputs, served_collection,
  *, expected_tenant, prior_backup)` returns an approval plan: refuses the
  production/default collection outright (`non_production_target` gate), validates
  the import manifest is clean (reuses `scripts.import_manifest.build_manifest`:
  no duplicate ids / tenant mismatch / collisions), records the **mandatory
  operator steps** (tenant-isolation check via `scripts/persistence_isolation_check.sh`,
  backup point via `scripts/backup.sh`), and provides a **rollback plan** (restore
  from the prior backup). `approval_report_markdown()` renders a safe report.
  `approved` is True only when the target is non-production **and** the manifest
  is clean.
- `scripts/promote_collection.py` (new, executable) — evaluation-only CLI; prints
  the approval report; exits 0 if approvable else 1. Never mutates a vectorstore.
- `tests/test_safe_collection_promotion.py` (new).

The actual ingest into the explicit non-production served collection and any
restore are performed by the existing tested tools (ingest path / `backup.sh` /
`restore.sh`) **after** approval — the chat runtime path is unchanged.

## Safety / no-secret / no-customer-data result

- Production/default collection is refused (tested for `""`, `default`, and the
  configured `VECTORSTORE_COLLECTION_NAME`). Planning performs **no vectorstore
  access** (tested: `get_vectorstore` not called). Synthetic data only; the
  report contains only safe fields (no secrets / raw document text / tenant
  data — scanned). No `.env`, Docker, deploy, or push.

## Verification results

- `tests/test_safe_collection_promotion.py` + `test_ingestion_ui_job_status.py` +
  `test_durable_multitenant_persistence.py`: **18 passed**.
- Full suite: **845 passed, 0 failed** (+6). `limited_beta_preflight.sh` exit 0.
  Full suite WAS run.

## What was not validated externally

- Live promotion into a real served collection and a live restore are exercised
  by the existing tools at operate time; this workflow is the gate/plan and is
  verified on synthetic data only.

## Deliverable paths

`webapi/collection_promotion.py`, `scripts/promote_collection.py`,
`tests/test_safe_collection_promotion.py`, this report.

## Git diff summary

3 new code/test files + this report. No change to product runtime / retrieval /
guard / auth / tenant / rate-limit / production_safe; no new dependencies; orphans
untouched.

## Final judgment: PASS

## Next recommendation

Prompt057 — real-customer-document PoC evaluation workflow (templates + tooling).
