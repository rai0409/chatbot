import type { IngestionJob } from "../api/ingestion";

export const JOBS: IngestionJob[] = [
  { job_id: "aaaaaaaa1111", status: "ok", mode: "dry_run", expected_tenant: "tenant_a", collection: "staging_v1", created_at: 1760000000 },
  { job_id: "bbbbbbbb2222", status: "running", mode: "raw_documents", expected_tenant: "tenant_a", collection: "staging_v1", created_at: 1760000100 },
];

export const JOB_DETAIL_OK: IngestionJob = { ...JOBS[0], ok: true, issue_counts: {} };
export const JOB_DETAIL_DONE: IngestionJob = { ...JOBS[1], status: "ok", ok: true };

export const UPLOAD_RESULT = { path: "/data/uploads/tenant_a/20260101/0123456789ab_manual.pdf", filename: "manual.pdf", bytes: 4096, sha256: "0".repeat(64) };
