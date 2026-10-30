import { vi } from "vitest";
import type { AccessOverview, AuditPage, Downtime, MetricsSummary, RecordStats } from "../api/overview";
import { STATUS, jsonResponse } from "./fixtures";

export { STATUS, jsonResponse };

export const METRICS: MetricsSummary = {
  counters: {
    chat_answer_mode_total: { grounded: 6, approved_exact_match: 2, fallback: 2 },
    chat_feedback_total: { good: 3, bad: 1 },
  },
  scope: "this_process",
  alerts: {
    overall: "WARN",
    signals: [
      { name: "fallback_rate", status: "WARN", value: 0.2, detail: "20% (2/10)" },
      { name: "error_rate", status: "OK", value: 0, detail: "0%" },
    ],
  },
};

export const HEALTH = {
  dependencies: { redis: { ok: true, ms: 3 }, qdrant: { ok: false, error: "timeout" } },
  embed_provider: "local",
  embed_model: "embed-model",
};

export const JOBS = { jobs: [{ job_id: "abcdef123456", status: "done", collection: "docs" }] };

export const RECORD_STATS: RecordStats = {
  days: 7,
  total: 40,
  answer_modes: { grounded: 30 },
  abstained: 4,
  degraded: 2,
  feedback: { good: 5, bad: 2 },
  verdicts: { correct: 3 },
  unreviewed_with_negative_feedback: 1,
};

export const DAILY = {
  days: 7,
  rows: [
    { day: "2026-10-27", calls: 10, errors: 1, input_tokens: 1000, output_tokens: 500, cost: 0.12 },
    { day: "2026-10-28", calls: 20, errors: 0, input_tokens: 2000, output_tokens: 900, cost: 0.25 },
  ],
};

export const USAGE = {
  days: 7,
  by: "tenant_id",
  rows: [
    { tenant_id: "sample", calls: 4, ok: 3, error: 1, refused: 0, breaker_open: 0, input_tokens: 100, output_tokens: 50, cost: 0.0123, latency_ms: 2000 },
  ],
};

export const DOWNTIME: Downtime = {
  days: 7,
  window_business_seconds: 7200 * 5,
  by_breaker: { "local:profile": { open_seconds: 3725, open_business_seconds: 1800, opened: 2 } },
  business_hours: { utc_offset_hours: 9, start: 9, end: 18, weekdays_only: true, holidays_known: false },
};

export const ACCESS: AccessOverview = {
  you: { reach: "tenants", tenants: ["sample"] },
  roles: [{ name: "admin", description: "管理者。すべての管理操作" }],
  admin_auth: { enabled: true, installation_credential: true, tenant_credentials: 1, tenants_with_own_admin: ["sample"], session_hours: 8 },
  api_auth: { enabled: true, keys: 2, tenant_map: { entries: 2, values: ["sample"] } },
  single_sign_on: { enabled: false, group_role_map: { entries: 0, values: [] }, group_tenant_map: { entries: 1, values: ["sample"] } },
  note: "利用者とグループは、ID 基盤で管理されます。",
};

export const AUDIT: AuditPage = {
  kind: "admin",
  total: 120,
  offset: 0,
  events: [
    { timestamp: "2026-10-28T01:02:03+00:00", action: "integration_switch", tenant_id: "sample", integration: "slack", enabled: false, actor: "山田太郎", note: "<b>x</b>" },
    { timestamp: "2026-10-28T00:00:00+00:00", action: "records_listed", days: 7 },
  ],
};

/** A fetch that answers by the start of the URL: a body (200) or a status number. Unknown URLs answer 404. */
export function routeFetch(routes: Record<string, unknown | number>) {
  return vi.fn((url: string, _init?: RequestInit) => {
    const key = Object.keys(routes)
      .filter((k) => String(url).startsWith(k))
      .sort((a, b) => b.length - a.length)[0];
    if (key === undefined) return Promise.resolve(jsonResponse({ detail: "no route" }, 404));
    const value = routes[key];
    return Promise.resolve(typeof value === "number" ? jsonResponse({ detail: "x" }, value) : jsonResponse(value));
  });
}
