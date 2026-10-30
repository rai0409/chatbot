// The shapes of /admin/system/*. They follow webapi/routers/system.py; a generated copy from the OpenAPI document
// replaces this file when the console grows (S5).
export type IntegrationState = "disabled" | "not_configured" | "failing" | "ok";

export interface Integration {
  name: string;
  label: string;
  state: IntegrationState;
  enabled: boolean;
  configured: boolean;
  layer: "" | "environment" | "switch" | "tenant" | "policy" | "breaker";
  reason: string;
}

export interface LlmProfile {
  breaker: "closed" | "open" | "half_open" | "disabled";
  model: string;
  location: "local" | "external";
  host: string;
}

export interface SystemStatus {
  tenant_id: string | null;
  revision: number;
  environment: { external_integrations: string[] };
  integrations: Integration[];
  llm: { profiles: Record<string, LlmProfile>; profiles_file_problems: string[] };
  warnings: string[];
  /** Absent from an older server. */
  approvals?: { required: number; pending: number; require_sso: boolean };
}

export interface SwitchRequest {
  name: string;
  enabled: boolean;
  reason: string;
  tenant_id?: string;
  expected_rev?: number;
  /** Who asks. Sent when turning on needs a second person's approval. */
  actor?: string;
}

export interface Person {
  name: string;
  id?: string | null;
  verified?: boolean;
}

export type ApprovalStatus = "pending" | "approved" | "rejected" | "cancelled" | "expired";
export type ApprovalDecision = "approve" | "reject" | "cancel";

/** A request to turn an integration on (rag_core/approvals.py). */
export interface ApprovalItem {
  id: string;
  kind: string;
  params: { name: string; tenant_id?: string | null };
  reason: string;
  requested_by: Person;
  requested_at: string;
  expires_at: string;
  status: ApprovalStatus;
  decided_by: Person | null;
  decided_at: string | null;
}

/** What a switch answers: the new state, or (HTTP 202) the request that now waits for a second person. */
export type SwitchResponse = SystemStatus & { change?: unknown; pending?: ApprovalItem };
