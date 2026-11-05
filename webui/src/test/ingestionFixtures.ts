import type { IngestSettings, IngestionJob } from "../api/ingestion";

export const JOBS: IngestionJob[] = [
  { job_id: "aaaaaaaa1111", status: "ok", mode: "dry_run", expected_tenant: "tenant_a", collection: "staging_v1", created_at: 1760000000 },
  { job_id: "bbbbbbbb2222", status: "running", mode: "raw_documents", expected_tenant: "tenant_a", collection: "staging_v1", created_at: 1760000100 },
];

export const JOB_DETAIL_OK: IngestionJob = { ...JOBS[0], ok: true, issue_counts: {} };
export const JOB_DETAIL_DONE: IngestionJob = { ...JOBS[1], status: "ok", ok: true };

export const UPLOAD_RESULT = { path: "/data/uploads/tenant_a/20260101/0123456789ab_manual.pdf", filename: "manual.pdf", bytes: 4096, sha256: "0".repeat(64) };

const base = { key: "", kind: "int" as const, minimum: 0, maximum: 1000, choices: [], unit: "", restart: "", by: null, reason: null, at: null };

/** A small, realistic answer of GET /admin/ingestion/settings (one or two settings of every group). */
export const SETTINGS: IngestSettings = {
  rev: 3,
  can_edit: true,
  groups: ["upload", "conversion", "pdf", "preprocess", "chunking"],
  settings: [
    { ...base, name: "INGEST_UPLOAD_MAX_CONCURRENT", group: "upload", default: 4, value: 4, source: "default", maximum: 256 },
    { ...base, name: "INGEST_CONVERT_MAX_MEMORY_MB", group: "conversion", default: 0, value: 4096, source: "override", maximum: 262144, unit: "MB", restart: "workers", by: "山田太郎", reason: "メモリの上限を設ける", at: "2026-11-01T09:00:00+00:00" },
    { ...base, name: "PDF_TABLES", kind: "bool", group: "pdf", default: true, value: true, source: "environment", minimum: null, maximum: null },
    { ...base, name: "INGEST_PDF_OCR", kind: "choice", group: "pdf", default: "auto", value: "auto", source: "default", minimum: null, maximum: null, choices: ["auto", "true", "false"] },
    { ...base, name: "DOCEXTRACT_HEADING_RATIO", kind: "float", group: "pdf", default: 1.08, value: 1.08, source: "default", minimum: 1.01, maximum: 2 },
    { ...base, name: "INGEST_PREPROCESS_REPEATED_MIN_PAGES", group: "preprocess", default: 3, value: 3, source: "default", minimum: 2, maximum: 50 },
    { ...base, name: "JA_CHUNK_TARGET_POLICY_MAX_CHARS", group: "chunking", default: 1200, value: 1200, source: "default", minimum: 40, maximum: 8000, unit: "chars" },
  ],
  history: [
    { rev: 3, at: "2026-11-01T09:00:00+00:00", name: "INGEST_CONVERT_MAX_MEMORY_MB", by: "山田太郎", reason: "メモリの上限を設ける", before: null, after: 4096 },
  ],
};
