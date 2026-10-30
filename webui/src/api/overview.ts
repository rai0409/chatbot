// The shapes and calls of the overview screens (dashboard, usage, access, audit). They follow webapi/main.py (metrics,
// health, ingestion jobs, logout-all) and webapi/routers/{usage,records,access,audit,system}.py.
import { request } from "./client";
import type { SystemStatus } from "./types";

export type AlertStatus = "OK" | "WARN" | "CRITICAL";

export interface AlertSignal {
  name: string;
  status: AlertStatus;
  value?: number | string | null;
  detail?: string;
}

export interface MetricsSummary {
  counters: Record<string, Record<string, number>>;
  scope: string;
  alerts: { overall: AlertStatus; signals: AlertSignal[] };
}

export interface Dependency {
  ok: boolean;
  ms?: number;
  error?: string;
}

export interface Health {
  dependencies?: Record<string, Dependency>;
  embed_provider?: string;
  embed_model?: string;
}

export interface IngestionJob {
  job_id?: string;
  status?: string;
  collection?: string;
}

export interface RecordStats {
  days: number;
  total: number;
  answer_modes: Record<string, number>;
  abstained: number;
  degraded: number;
  feedback: Record<string, number>;
  verdicts: Record<string, number>;
  unreviewed_with_negative_feedback: number;
}

export interface DailyUsage {
  day: string;
  calls: number;
  errors: number;
  input_tokens: number;
  output_tokens: number;
  cost: number;
}

export const USAGE_GROUPS = ["tenant_id", "feature", "profile", "model", "key_fingerprint", "location"] as const;
export type UsageGroup = (typeof USAGE_GROUPS)[number];

export interface UsageRow {
  calls: number;
  ok: number;
  error: number;
  refused: number;
  breaker_open: number;
  input_tokens: number;
  output_tokens: number;
  cost: number;
  latency_ms: number;
  [group: string]: string | number;
}

export interface Downtime {
  days: number;
  window_business_seconds: number;
  by_breaker: Record<string, { open_seconds: number; open_business_seconds: number; opened: number }>;
  business_hours: { utc_offset_hours: number; start: number; end: number; weekdays_only: boolean; holidays_known: boolean };
}

/** GET /admin/availability: the business-hours availability from the log of the external probe (rag_core/availability.py). */
export interface Availability {
  configured: boolean;
  hint?: string;
  log_file_found?: boolean;
  probes?: number;
  availability_percent?: number | null;
  meets_target?: boolean | null;
  target_percent?: number;
  coverage_percent?: number | null;
  business_minutes?: { total: number; up: number; down: number; unmonitored: number; degraded_but_up: number };
  outages?: { start: string; end: string | null; business_minutes: number; error: string }[];
  unmonitored_gaps?: { start: string; end: string | null; business_minutes: number }[];
}

export interface MappingCount {
  entries: number;
  values: string[];
}

export interface AccessOverview {
  you: { reach: "installation" | "tenants"; tenants: string[] | null };
  roles: { name: string; description: string }[];
  admin_auth: {
    enabled: boolean;
    installation_credential: boolean;
    tenant_credentials: number;
    tenants_with_own_admin: string[];
    session_hours: number;
  };
  api_auth: { enabled: boolean; keys: number; tenant_map: MappingCount };
  single_sign_on: { enabled: boolean; group_role_map: MappingCount; group_tenant_map: MappingCount };
  note: string;
}

export type AuditKind = "admin" | "chat" | "feedback";
export type AuditEvent = Record<string, unknown>;

export interface AuditPage {
  kind: AuditKind;
  total: number;
  offset: number;
  events: AuditEvent[];
}

export interface AuditVerify {
  ok: boolean;
  kind?: string;
  checked?: number;
  file?: string;
  line?: number;
  error?: string;
}

export const AUDIT_PAGE_SIZE = 50;

const query = (params: Record<string, string | number | undefined>) => {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== undefined && value !== "") search.set(key, String(value));
  return search.toString();
};

export const overviewApi = {
  metrics: () => request<MetricsSummary>("/admin/metrics/summary"),
  health: () => request<Health>("/admin/health"),
  jobs: () => request<{ jobs: IngestionJob[] }>("/admin/ingestion/jobs"),
  systemStatus: () => request<SystemStatus>("/admin/system/status"),
  recordStats: (days: number) => request<RecordStats>(`/admin/records/stats?days=${days}`),
  usageDaily: (days: number) => request<{ days: number; rows: DailyUsage[] }>(`/admin/usage/daily?days=${days}`),
  usage: (days: number, by: UsageGroup) => request<{ days: number; by: string; rows: UsageRow[] }>(`/admin/usage?days=${days}&by=${by}`),
  downtime: (days: number) => request<Downtime>(`/admin/usage/downtime?days=${days}`),
  availability: (days: number) => request<Availability>(`/admin/availability?days=${days}`),
  access: () => request<AccessOverview>("/admin/access"),
  audit: (p: { kind: AuditKind; limit: number; offset: number; text?: string; action?: string; since?: string }) =>
    request<AuditPage>(`/admin/audit?${query(p)}`),
  auditVerify: (kind: AuditKind) => request<AuditVerify>(`/admin/audit/verify?kind=${kind}`),
  logoutAll: () => request<{ ok: boolean }>("/admin/logout-all", { method: "POST" }),
};
