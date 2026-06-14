# Prompt067: Vectorstore Production Backend Decision (Chroma vs pgvector vs Qdrant)

ANALYSIS / decision only. **No migration, no new dependency, no runtime change.**
Decides the production vectorstore backend for the first annual contract.

## 1. Preconditions

- `rag_core/store.py` uses single-node local Chroma (`PersistentClient`,
  cached per path; cosine HNSW; embedding-fingerprint verification on collection).
- Tenant filtering goes through `rag_core/retrieval._build_base_where` /
  `_to_chroma_where` (Prompt035): a flat internal form translated to Chroma's
  `$and`/`$eq` where-dialect. Targeted tests green (13 passed).

## 2. Current store coupling

- **Coupled to Chroma**: `chromadb.PersistentClient`, `get_or_create_collection`,
  `metadata={"hnsw:space": "cosine"}`, fingerprint stamping, and the
  Chroma-specific **where-clause dialect** produced by `_to_chroma_where`.
- **Already abstracted**: callers go through `get_vectorstore()` + the retrieval
  filter builder rather than raw Chroma calls — the internal where-form is flat
  and only translated to Chroma at the edge. This is the main portability asset.

## 3. Adapter-boundary readiness

- The flat internal where-form (`_build_base_where`) is backend-agnostic; only
  `_to_chroma_where` and the `store.py` client calls are Chroma-specific.
- A migration would implement: (a) a client/collection adapter, (b) a
  filter-dialect translator (flat-form → pgvector SQL `WHERE` / Qdrant filter),
  (c) embedding-fingerprint equivalence, (d) tenant-isolation parity tests
  mirroring `test_durable_multitenant_persistence.py`.
- Gap: there is no formal `VectorStore` interface yet — the boundary is
  convention, not an enforced protocol. Defining that interface is the first
  done-criterion for any spike.

## 4. On-prem / offline fit

| Backend | On-prem/offline | Footprint | Notes |
| --- | --- | --- | --- |
| **Chroma (current)** | excellent — embedded, no server | smallest; single dir | matches single-node, easy backup/restore (Prompt063) |
| **pgvector** | good — Postgres on the host | medium; needs Postgres ops | SQL filters, transactional, familiar ops, replication path |
| **Qdrant** | good — runs as a service | medium/large; separate service | rich filtering, built-in replication/sharding, another service to operate |

## 5. Ops / HA implications

- **Chroma**: simplest ops; **cannot be safely shared across replicas** (local
  dir) → active-active is blocked on it (ties to Prompt066). HA = active-passive
  via backup/restore only.
- **pgvector**: inherits Postgres replication/backup → enables active-passive and
  a path to shared-state active-active; adds DB administration.
- **Qdrant**: native replication/sharding → best scale-out story; adds a new
  stateful service + its own backup/upgrade lifecycle.

## 6. Migration risk

- Filter-dialect parity (tenant isolation correctness) is the highest-risk area —
  any translation bug is a **cross-tenant exposure risk**. Must be covered by
  parity tests before cutover.
- Embedding/fingerprint semantics, distance-metric equivalence (cosine), and
  re-indexing the full corpus are additional risk/effort.
- New dependency + new ops surface (Postgres or Qdrant) for an on-prem customer.

## 7. Recommendation

- **Stay on Chroma for the first annual contract.** It best fits single-node,
  on-prem/offline, small footprint, and the existing tested DR/backup story. No
  measured capacity problem justifies migration yet.
- **Trigger a migration spike only when** a customer requires active-active HA or
  exceeds single-node capacity (signals from Prompt066). Prefer **pgvector** if
  the customer already runs Postgres / wants SQL-native ops; prefer **Qdrant** if
  native scale-out/replication is the priority.

### Done-criteria for a future migration spike

1. A defined `VectorStore` interface (client + filter translator + fingerprint).
2. Filter-dialect translator with **tenant-isolation parity tests** equivalent to
   the current suite (no cross-tenant leakage).
3. Corpus re-index + retrieval-quality equivalence on a synthetic corpus.
4. Backup/restore + DR drill parity for the new backend.
5. Documented ops runbook (install/upgrade/backup) for the new service.

## 8. What is NOT claimed / validated

- No migration performed; no benchmark on real customer data; no HA claim. All
  comparisons are design analysis. Backend trade-offs are general, not a
  competitor-superiority claim.

## Verification results

- Targeted: `test_chroma_where_builder.py` + `test_durable_multitenant_persistence.py`
  → **13 passed**.
- `pytest --collect-only -q`: **860 collected**. Full suite **not run** (analysis-
  only; no source change).

## Deliverable / diff

- New: this report only. Orphan files untouched. No code/config/threshold change.

## Final judgment: PASS

## Next recommendation

Prompt068 — cross-encoder rerank promotion decision (analysis; no setting change).
