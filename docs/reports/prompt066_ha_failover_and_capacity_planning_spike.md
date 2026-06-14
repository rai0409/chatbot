# Prompt066: HA / Failover / Capacity-Planning Spike

ANALYSIS / spike only. No deploy, no Docker, no new infra, no runtime change.
KuraDen today is a **single-node** app (FastAPI + local Chroma). This report maps
the HA/failover/capacity path: what is locally achievable, what needs
infrastructure, and what stays future. **No HA is claimed.**

## 1. Preconditions

- Single-node deployment is the only validated topology (Prompts012, 056–065).
- Metrics are per-process (`webapi/metrics_registry.py`); alerting via
  `webapi/alerting.py` + `scripts/alert_check.py`.

## 2. Failure modes (single-node today)

| Failure | Blast radius | Current mitigation | Gap |
| --- | --- | --- | --- |
| Process crash | full outage | systemd/process-manager restart | no second node |
| Host failure | full outage + data-at-risk | backup/restore (Prompt063 DR drill) | RTO = manual restore time |
| Vectorstore corruption | wrong/no answers | sha256-verified backup + restore | detection is manual |
| Disk full | writes fail | capacity alert (below) | needs monitoring discipline |
| LLM backend down | fallback/abstain path | abstain-first guard, timeouts | upstream dependency |
| Key/secret leak | auth bypass risk | rotate keys, revert tag | operator-run |

## 3. Stateless vs stateful components

- **Stateless** (horizontally scalable in principle): the FastAPI request path,
  retrieval/guard/citation logic, answer cache (per-process, rebuildable).
- **Stateful** (the HA hard part): the Chroma vectorstore (local persistent dir),
  approved-Q&A store, audit log, session/OIDC state. These pin the app to one
  node today; true HA requires externalizing or replicating them.

## 4. Per-process metric caveat (critical for HA)

`metrics_registry` counters are **per-process, in-memory**. With >1 replica each
process exposes only its own counts; Prometheus would scrape each separately and
sums must be done at the query layer. Any HA/scale-out step MUST treat metrics as
per-replica — a single in-process counter is not a cluster-wide truth. Alert
thresholds (`DEFAULT_THRESHOLDS`) are likewise per-process and would need
per-replica or aggregated evaluation.

## 5. Capacity signals from EXISTING metrics

No new instrumentation needed to start capacity planning — derive from current
counters: request count + latency (p50/p95) for throughput/headroom, error/
fallback rate for saturation, cache hit-rate for memory pressure, and disk usage
of the vectorstore dir for storage growth. Recommend a documented capacity
review using these signals (no code change here).

## 6. HA options (cost / complexity)

| Option | What it buys | Cost / complexity | Verdict |
| --- | --- | --- | --- |
| **Active-passive** (warm standby + restore-on-failover) | host-failure recovery, modest RTO | medium: standby host + replication of vectorstore/backups + runbook | **Recommended first step** when a customer needs HA |
| **Active-active behind LB** (stateless replicas, shared/external vectorstore) | throughput + node-failure tolerance | high: requires externalized vectorstore (see Prompt067 pgvector/Qdrant), shared session store, aggregated metrics | future; gated on backend decision |
| **Managed/clustered vectorstore** | scale + replication | high: new infra + ops + dependency | future |

## 7. Recommendation

- **Stay single-node by default**; offer **active-passive** as the first HA tier
  only when a customer requires it, building on the existing DR drill (Prompt063)
  for the failover-restore step.
- **Do not** claim HA, 24×7, or scale-out now. Active-active is **blocked on the
  vectorstore backend decision (Prompt067)** because the local Chroma dir cannot
  be safely shared across replicas.
- Add a capacity-review note using existing metrics; defer new infra.

## 8. What is NOT validated / NOT claimed

- No HA, no failover SLA, no multi-node test performed. All options are design
  analysis; none deployed. Per-process metrics make any multi-replica claim
  premature until aggregation is built.

## Verification

- `git status --short`: prompt-scoped (this report only; orphans untouched).
- `pytest --collect-only -q`: **860 collected**. Full suite **not run** (analysis-
  only; no source change).

## Final judgment: PASS

## Next recommendation

Prompt067 — vectorstore production backend decision (pgvector / Qdrant).
