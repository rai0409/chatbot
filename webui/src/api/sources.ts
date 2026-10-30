// Calls of the document sources screen (webapi/main.py, "Document sources (5-4)").
import { request } from "./client";

export interface PlanCounts {
  added: number;
  modified: number;
  deleted: number;
}

export interface Guard {
  hold: boolean;
  reasons: string[];
}

export interface SourceState {
  last_error?: string | null;
  last_scan_at?: string | null;
  last_apply_at?: string | null;
  [key: string]: unknown;
}

export interface SourceRow {
  source_id: string;
  tenant_id: string;
  kind: string;
  collection: string;
  schedule_minutes: number;
  enabled: boolean;
  state: SourceState;
  /** null until the first scan. */
  pending: PlanCounts | null;
  guard: Guard | null;
}

export interface SourcePlan {
  added: string[];
  modified: string[];
  deleted: string[];
  conflicts: Record<string, string[]>;
  counts: PlanCounts;
  applied_files: number;
  total_files: number;
  plan_sha256: string;
  guard: Guard;
}

/** The answer of a scan: the counts, or {error} (integration_disabled carries the reason code). */
export interface ScanResult {
  run_id?: string;
  error?: string;
  reason?: string;
  counts?: PlanCounts;
  total_files?: number;
  skipped?: Record<string, number>;
  name_conflicts?: number;
}

export interface ApplyResult {
  status: string;
  reasons?: string[];
  counts?: PlanCounts;
  plan_sha256?: string;
  run_id?: string;
  ingested?: number;
  deleted?: number;
  failed?: unknown[];
  conflicts?: Record<string, string[]>;
}

export interface ChangeEntry {
  seq: number;
  at: string;
  kind: string;
  run_id: string;
  summary: Record<string, unknown> | null;
}

export interface CreateBody {
  source_id: string;
  tenant_id: string;
  kind: string;
  schedule_minutes: number;
  path?: string;
  options?: { drive_id: string; folder: string };
  collection?: string;
}

const JSON_HEADERS = { "Content-Type": "application/json" };
const base = (id: string) => `/admin/sources/${encodeURIComponent(id)}`;

function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, { method: "POST", headers: JSON_HEADERS, body: body === undefined ? undefined : JSON.stringify(body) });
}

export const sourcesApi = {
  list: () => request<{ sources: SourceRow[] }>("/admin/sources/list"),
  create: (body: CreateBody) => post<SourceRow>("/admin/sources", body),
  scan: (id: string) => post<ScanResult>(`${base(id)}/scan`),
  plan: (id: string) => request<SourcePlan>(`${base(id)}/plan`),
  apply: (id: string, approval?: string) => post<ApplyResult>(`${base(id)}/apply`, approval ? { approval } : {}),
  approve: (id: string, planSha256: string) => post<{ approved: string; by: string; run_id: string }>(`${base(id)}/approve`, { plan_sha256: planSha256 }),
  setEnabled: (id: string, enable: boolean) => post<SourceRow>(`${base(id)}/${enable ? "enable" : "disable"}`),
  remove: (id: string) => post<{ removed: string }>(`${base(id)}/remove`),
  changes: (id: string) => request<{ entries: ChangeEntry[] }>(`${base(id)}/changes`),
};
