import type { Assignments, LimitsSnapshot } from "../api/limits";

export { jsonResponse } from "./fixtures";

export const LIMITS: LimitsSnapshot = {
  day_starts_at: "2026-10-28T15:00:00+00:00",
  warn_ratio: 0.8,
  limits: [
    { name: "user_daily_calls", scope: "user", unit: "calls", value: 100, source: "global", environment_default: 0 },
    { name: "user_daily_tokens", scope: "user", unit: "tokens", value: 0, source: "environment", environment_default: 0 },
    { name: "tenant_daily_tokens", scope: "tenant", unit: "tokens", value: 50000, source: "environment", environment_default: 50000 },
    { name: "tenant_daily_cost", scope: "tenant", unit: "cost", value: 10, source: "tenant", environment_default: 0 },
    { name: "global_daily_cost", scope: "global", unit: "cost", value: 0, source: "environment", environment_default: 0 },
  ],
  tenants: {
    "tenant-a": { calls: 120, tokens: 52000, cost: 2.5 },
    "tenant-b": { calls: 5, tokens: 900, cost: 0.1 },
  },
  users: [
    { tenant_id: "tenant-a", user: "abcdef123456", calls: 100, tokens: 40000, cost: 2 },
    { tenant_id: "tenant-b", user: "zyxwvu654321", calls: 3, tokens: 500, cost: 0.05 },
  ],
  global: { calls: 125, tokens: 52900, cost: 2.6 },
  warnings: [
    { limit: "tenant_daily_tokens", scope: "tenant", id: "tenant-a", used: 52000, allowed: 50000, ratio: 1.04 },
    { limit: "user_daily_calls", scope: "user", id: "tenant-a/abcdef123456", used: 85, allowed: 100, ratio: 0.85 },
  ],
};

export const EMPTY_LIMITS: LimitsSnapshot = { ...LIMITS, tenants: {}, users: [], global: {}, warnings: [] };

export const ASSIGNMENTS: Assignments = {
  tiers: ["standard", "premium"],
  profiles: ["cloud", "local"],
  roles: ["admin", "operator", "user", "viewer"],
  profiles_file_configured: true,
  assignments: [
    { kind: "global", id: null, tier: "standard", by: "田中太郎", at: "2026-10-01T03:00:00+00:00", reason: "既定", defined: true },
    { kind: "role", id: "operator", tier: "gold", by: "佐藤次郎", at: "2026-10-02T03:00:00+00:00", reason: "試行", defined: false },
  ],
};

export const NO_TIERS: Assignments = { tiers: [], profiles: [], roles: ["admin"], profiles_file_configured: false, assignments: [] };
