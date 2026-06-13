# Prompt030: Durable Multi-Tenant Persistence Verification

Implementation report. Adds automated verification — synthetic data only, on a
non-production collection — that tenant isolation and stored data survive a
Chroma client reload and a hash-verified backup/restore. No storage backend was
introduced and no retrieval/guard/cross-encoder/distance behavior changed.

## What was verified

- **Reload durability**: after building a synthetic two-tenant collection and
  dropping the cached client (`reset_vectorstore_clients`, simulating a process
  restart), a fresh `PersistentClient` reads the same records from disk
  (`collection.count() == 4`) and the embedding fingerprint is still
  present/valid.
- **Reload tenant isolation**: a `tenant_alpha` query returns only `alpha-*`
  chunks and never `beta-*`, and vice versa — including when `tenant_alpha`
  queries with `tenant_beta`'s embedding (proving the `tenant_id` `where`
  filter + `_tenant_matches` post-filter isolate regardless of embedding
  similarity).
- **Backup/restore durability + isolation**: `scripts/backup.sh` archives the
  synthetic source store; `scripts/restore.sh` restores into a separate temp
  target with `sha256sum -c` verification (non-destructive staging). The
  restored store re-opens with all records present, a valid fingerprint, and
  tenant isolation intact.
- **Fingerprint**: `stamp_collection_fingerprint` / `collection_fingerprint` /
  `verify_collection_fingerprint` round-trip survives reload and restore.

## What remains unchanged

- `rag_core/store.py`, `rag_core/retrieval.py`, the guard, cross-encoder
  settings, distance thresholds, tenant authorization/isolation semantics, and
  rate-limiter semantics are untouched. The work is test + a thin safe script +
  docs only.
- No new dependencies. The production/default vectorstore and default
  collection are never opened or mutated.

## Non-production / synthetic-data safety

- All tests set `config.VECTORSTORE_DIR` to a pytest `tmp_path` and
  `config.VECTORSTORE_COLLECTION_NAME` to `pilot_persist_check_v1`, asserting
  it differs from the production/default collection name (captured at import
  before any monkeypatch) and that the active store dir is not the repo
  `vectorstore/`.
- Records are four deterministic synthetic chunks (two `tenant_alpha`, two
  `tenant_beta`) with fixed 3-dim embeddings — no network, no model download.
- `EMBED_PROVIDER=local` / `LOCAL_EMBED_MODEL=test-local-model` are set in-test
  so the fingerprint is deterministic (same pattern as the existing
  `test_embedding_fingerprint.py`).
- `scripts/persistence_isolation_check.sh` runs only these tests, never reads
  `.env`, and unsets any ambient store pointers before running.

## Test evidence

`tests/test_durable_multitenant_persistence.py` (5 tests, all passing):

- `test_reload_preserves_records_and_fingerprint`
- `test_reload_preserves_tenant_isolation`
- `test_backup_restore_preserves_records_fingerprint_and_isolation`
- `test_uses_non_production_collection_and_temp_dir`
- `test_no_secrets_or_raw_keys_in_outputs`

Regression suites remained green (tenant isolation, embedding fingerprint,
deploy-ops, chat tenant profile, API-key tenant authorization, full collection,
product readiness smoke, limited-beta preflight, smoke/qa_pair evals).

## Chroma where-clause observation (out of scope, surfaced for follow-up)

The installed `chromadb` validates that a `collection.query(where=...)` dict has
exactly one operator and **rejects a multi-key `where`** such as
`{"searchable": 1, "tenant_id": "tenant_alpha"}` unless wrapped in `$and`.
`rag_core.retrieval._build_base_where` produces exactly that two-key shape for a
non-default tenant when `searchable` filtering is on. To exercise the real
retrieval/isolation path against real Chroma without changing retrieval
semantics (explicitly out of scope here), the tests set the existing
`config.IGNORE_SEARCHABLE` flag so the tenant `where` is single-key
(`{"tenant_id": ...}`); the `tenant_id` filter plus the authoritative
`_tenant_matches` post-filter still fully isolate tenants.

This is a genuine finding worth a dedicated, in-scope fix later (wrap multi-key
`where` in `$and` in `_build_base_where`, with regression tests), but it is a
retrieval-semantics change and therefore deliberately not made in this
verification prompt.

## Remaining production risks

- **Single-node only**: this proves durability/isolation for the local Chroma
  `PersistentClient`. Managed/HA persistence, replication, and failover are not
  covered.
- **Concurrent writers / scale**: the proof is single-process, small-data. Hot
  backup consistency under live writes and behavior at corpus scale are not
  established (see the backup consistency note in `docs/operations.md`).
- **Multi-key `where` under real Chroma**: see the observation above — a
  follow-up should make `_build_base_where` `$and`-safe so non-default-tenant
  vector queries with the `searchable` filter work without `IGNORE_SEARCHABLE`.
- **Managed multi-tenant data lifecycle** (per-tenant deletion/offboarding at
  the store level) remains a general-production item.
