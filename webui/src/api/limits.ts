// Calls and types of the limits and model assignments screen.
import { request } from "./client";

export type LimitName = "user_daily_calls" | "user_daily_tokens" | "tenant_daily_tokens" | "tenant_daily_cost" | "global_daily_cost";
export const LIMIT_NAMES: LimitName[] = ["user_daily_calls", "user_daily_tokens", "tenant_daily_tokens", "tenant_daily_cost", "global_daily_cost"];
export const GLOBAL_ONLY: LimitName[] = ["global_daily_cost"];

export interface LimitRow {
  name: string;
  scope: "user" | "tenant" | "global";
  unit: "calls" | "tokens" | "cost";
  value: number;
  source: "environment" | "global" | "tenant";
  environment_default: number;
}

export interface UseTotals {
  calls: number;
  tokens: number;
  cost: number;
}

export interface UserUse extends UseTotals {
  tenant_id: string;
  /** The fingerprint of the key, or of the person with single sign-on. */
  user: string;
}

export interface LimitWarning {
  limit: string;
  scope: "user" | "tenant" | "global";
  id: string | null;
  used: number;
  allowed: number;
  ratio: number;
}

export interface LimitsSnapshot {
  day_starts_at: string;
  warn_ratio: number;
  limits: LimitRow[];
  tenants: Record<string, UseTotals>;
  users: UserUse[];
  global: Partial<UseTotals>;
  warnings: LimitWarning[];
}

export interface LimitChange {
  name: string;
  /** null removes the setting; 0 means no limit. */
  value: number | null;
  reason: string;
  tenant_id?: string;
}

export type AssignmentKind = "global" | "tenant" | "role" | "user";
export const ASSIGNMENT_KINDS: AssignmentKind[] = ["global", "tenant", "role", "user"];

export interface Assignment {
  kind: AssignmentKind;
  id: string | null;
  tier: string | null;
  by: string | null;
  reason: string | null;
  at: string | null;
  /** false: the tier is no longer in the profiles file. */
  defined: boolean;
}

export interface Assignments {
  tiers: string[];
  profiles: string[];
  assignments: Assignment[];
  roles: string[];
  profiles_file_configured: boolean;
}

export interface AssignmentChange {
  kind: AssignmentKind;
  id?: string;
  /** null removes the assignment. */
  tier: string | null;
  reason: string;
}

const json = (method: string, body: unknown): RequestInit => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export const limitsApi = {
  limits: (tenantId?: string) => request<LimitsSnapshot>(tenantId ? `/admin/limits?tenant_id=${encodeURIComponent(tenantId)}` : "/admin/limits"),
  setLimit: (body: LimitChange) => request<{ change: unknown } & LimitsSnapshot>("/admin/limits", json("POST", body)),
  assignments: () => request<Assignments>("/admin/llm-assignments"),
  setAssignment: (body: AssignmentChange) => request<{ change: unknown } & Assignments>("/admin/llm-assignments", json("POST", body)),
};
