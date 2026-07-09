# Prompt035: Chroma `$and`-safe `where` fix

Implementation report. Fixes the multi-key Chroma `where` issue found during
Prompt030 durable-multitenant persistence verification, where a flat
multi-condition filter (for example `{"searchable": 1, "tenant_id": "..."}`)
is rejected by the installed Chroma `where` validator (it requires exactly one
top-level operator). Tenant isolation and `searchable` filtering semantics are
preserved exactly.

## Files changed

- `rag_core/retrieval.py`
  - Added `_to_chroma_where(where)`: a boundary converter that maps a flat
    multi-condition filter to the Chroma-safe single-`$and` form
    (`{"$and": [{k: v}, ...]}`), passes a single-condition filter through
    unchanged, and maps an empty/`None` filter to `None` (no filter).
  - Applied it at the two Chroma call-sites only:
    - `vector_retrieve` → `collection.query(..., where=_to_chroma_where(where))`
    - neighbor lookup → `collection.get(where=_to_chroma_where({"doc_id": ...,
      "chunk_index": ...}))`
  - `_build_base_where` is unchanged and still returns the flat dict, which the
    in-memory `_meta_matches_where` filter and the keyword path continue to
    consume.
- `tests/test_chroma_where_builder.py` (new) — focused converter regression
  tests.
- `tests/test_tenant_isolation.py` — updated `test_vector_tenant_filtering` to
  assert the Chroma-safe `$and` boundary form (added a `_flatten_where` helper);
  isolation assertions preserved.
- `docs/reports/prompt035_chroma_and_safe_where_fix.md` (this report).

## Exact behavior fixed

- Before: a non-default tenant query with `searchable` filtering produced a flat
  two-key `where` (`{"searchable": 1, "tenant_id": "t"}`) handed directly to
  `collection.query`; the current Chroma rejects it ("Expected where to have
  exactly one operator"). The neighbor `collection.get(where={"doc_id": ...,
  "chunk_index": ...})` had the same two-key problem (silently swallowed by its
  `except: continue`, so neighbor expansion returned nothing).
- After: multi-condition filters are emitted as `{"$and": [{...}, {...}]}`
  (matching the convention already used by `rag_core/approved_similar._qa_pair_where`).
  Single-condition filters are emitted as-is. Empty filters become `None`.
- No condition is added or removed by the conversion, so retrieval is not
  broadened and tenant isolation is not weakened. The default tenant still adds
  no `tenant_id` clause and relies on the authoritative post-query
  `_tenant_matches` filter; non-default tenants keep their strict `tenant_id`
  equality clause.

## Tests run and results

- `tests/test_chroma_where_builder.py` + `tests/test_tenant_isolation.py`:
  **16 passed**.
- `tests/test_durable_multitenant_persistence.py` +
  `tests/test_enduser_chat_ui.py` + `tests/test_retrieval_ja_integration.py` +
  `tests/test_retrieval_batching.py` + `tests/test_chat_stream.py`:
  **31 passed**.
- Full suite: **735 passed, 0 failed** (collect-only: 735 collected; +8 new
  tests vs prior 727).
- `scripts/product_readiness_smoke.sh`: exit 0 (117 passed).
- `scripts/limited_beta_preflight.sh`: exit 0 (PREFLIGHT OK).

## Confirmation of non-changes

The following were NOT changed or accessed:

- `.env` was not read; no secrets printed or inferred; no `.env` model names used.
- No production/default vectorstore data mutated; tests use synthetic data only
  (no real customer data).
- Docker not run; nothing deployed; nothing pushed remotely.
- API auth, tenant **authorization** semantics, tenant **isolation** semantics
  (clauses preserved + post-query `_tenant_matches` intact), and rate-limiter
  semantics unchanged.
- `production_safe` profile behavior, global distance thresholds, and
  cross-encoder settings unchanged.
- Chat UI, feedback, and streaming behavior unchanged (verified by
  `test_enduser_chat_ui.py` and `test_chat_stream.py`).
- No new dependencies added.

## Notes

- The Prompt030 durable-persistence test set `IGNORE_SEARCHABLE=True` as a
  workaround for this very issue; with this fix that workaround is no longer
  required for correctness, but it was left untouched (out of scope).
