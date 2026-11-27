# Backup scope (stage 14)

`scripts/backup.sh` archives the files the API needs to serve again; the
external services keep their data elsewhere and are backed up with their own
tools. Restore with `scripts/restore.sh` (hash-verified), then restore the
services, then start the API.

| Data | Where | How |
|---|---|---|
| Chroma vectors (`VECTOR_BACKEND=chroma`) | `vectorstore/` | `backup.sh` (SQLite part as an online snapshot; run while no ingestion job writes) |
| Keyword corpora (served and staging) | `index/`, `index/collections/` | `backup.sh` (`index/.keyword_cache/` is rebuilt, not archived) |
| Staging collections, owners, jobs | `runs/ingestion_registry.sqlite3` or Postgres | `backup.sh` / `pg_dump` |
| Session revocations, conversations (local backends) | `runs/state/`, `runs/conversations/` | `backup.sh` |
| Approved answers | `data/approved_qa/`, the `APPROVED_QA_PATH` file | `backup.sh` (`--extra <path>` when outside `data/approved_qa/`) |
| Audit logs | `runs/audit/` | `backup.sh` |
| Product / RAG profiles | `configs/` | `backup.sh` |
| Uploaded originals | the upload directory | `backup.sh --extra <dir>` if re-ingesting from the originals must stay possible (they are pruned after `INGEST_UPLOAD_RETENTION_DAYS`) |
| Qdrant vectors (`VECTOR_BACKEND=qdrant`) | Qdrant server | `POST /collections/<name>/snapshots` for every collection, copy the snapshot files |
| Postgres state (`*_BACKEND=postgres`) | Postgres | `pg_dump --format=custom` |
| Redis state (`*_BACKEND=redis`) | Redis | rate limits and the answer cache need no backup; session revocations expire with the sessions, so after a restore of an older Redis copy run "end all admin sessions" |

The vector store and the keyword corpus must come from the same backup: they
are checked against each other with `scripts/check_index_consistency.py`
after a restore. `scripts/dr_drill.sh` only proves that an archive round-trips;
a drill should also start the API on the restored data and run a search.


Composeのバックアップ出力先は永続ボリュームに保存し、`config/rag_profiles` をアーカイブ対象とする。Qdrant、PostgreSQL、Redisのデータは別途それぞれの手順で保護する。

## 2026-11 hardening

- `scripts/backup.sh` takes `<output>/.backup.lock` (one backup at a time) and the maintenance lock exclusively
  (`MAINTENANCE_LOCK_FILE`, default `runs/locks/maintenance.lock`; ingestion jobs, promotion and deletion hold it shared),
  re-verifies the finished archive (every member re-hashed against the manifest; a bad archive is kept as `.failed`)
  and writes `<output>/backup_status.json` (monitored: failure and age, `BACKUP_MAX_AGE_HOURS`).
- Also archived when present: `models_cache/`, `config/embedding_assets/`, and a non-default `SOURCES_DIR` /
  `ACTIVE_GENERATION_FILE` / index directory under `--source-dir` (outside it: refused unless `BACKUP_SKIP_OUTSIDE=1`).
- `--prune` lists the archives beyond `BACKUP_KEEP`; it deletes them only with `BACKUP_PRUNE_CONFIRM=1`.
- The console's backup button uses the same file lock as cron (not a per-process flag).
- After a restore, flush the answer cache (restart the API; cached answers may quote data
  that the restored state no longer has).

### Conversation history storage v2

- Local history uses `runs/conversations/.v2/` (digest-based tenant, identity and
  thread keys). Preserve its `.redactions.json` markers together with the history.
- PostgreSQL history uses the `v2:` tenant namespace in `conversation_turns` and
  the `conversation_redactions_v2` table. Back up and restore both in the same
  database snapshot. Do not drop the marker table or restore history alone.
- Legacy history remains on disk/in the database but is not read by v2. Do not
  infer ownership from the old, lossy keys or automatically copy legacy rows.
- A restore from before a deletion may also predate its marker. Reconcile the
  deletion records before serving restored conversation history. Keep history
  disabled until that reconciliation is verified.
