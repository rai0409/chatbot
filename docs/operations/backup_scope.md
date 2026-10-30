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
