// The contract management API (webapi/main.py, "Contracts (7-3)"). The shapes follow rag_core/contracts.py.
import { request } from "./client";

export type FieldState = "extracted" | "confirmed" | "corrected" | "missing";
export type ContractStatus = "needs_review" | "active" | "expired";

export interface Evidence {
  page: number | null;
  text: string;
}

export interface ContractField {
  value: string | number | boolean | null;
  state: FieldState;
  confidence: number | null;
  evidence: Evidence | null;
  updated_by: string | null;
  updated_at: string | null;
  note?: string;
}

export interface Deadline {
  kind: string;
  label: string;
  date: string;
  days_left: number;
  basis: string[];
  verified: boolean;
}

export interface UpcomingDeadline extends Deadline {
  contract_id: string;
  counterparty: string | null;
  title: string | null;
  status: ContractStatus;
}

export interface ContractSummary {
  contract_id: string;
  counterparty: string | null;
  title: string | null;
  status: ContractStatus;
  source_doc: string;
  nearest_deadline: Deadline | null;
  unconfirmed: number;
}

export interface HistoryEntry {
  at: string;
  by: string;
  action: string;
  field: string | null;
  old?: string | null;
  new?: string | null;
}

export interface ContractDetail {
  contract_id: string;
  source_doc: string;
  fields: Record<string, ContractField>;
  history: HistoryEntry[];
  status: ContractStatus;
  deadlines: Deadline[];
  required: string[];
}

export interface ImportResult {
  contract: ContractSummary;
  rejected: Record<string, string>;
  kept: string[];
}

export interface NotifyRow {
  key: string;
  contract_id: string;
  kind: string;
  date: string;
  offset: number;
  verified: boolean;
  status: "due" | "sent" | "failed";
  reason?: string;
}

export interface NotifyResult {
  due: number;
  sent: number;
  failed: number;
  dry_run: boolean;
  results: NotifyRow[];
}

export interface DraftResult {
  draft_id?: string;
  kind: string;
  date: string;
  [key: string]: unknown;
}

const JSON_HEADERS = { "Content-Type": "application/json" };
const q = (tenant: string) => `?tenant_id=${encodeURIComponent(tenant)}`;
const one = (id: string) => encodeURIComponent(id);
const post = <T>(path: string, body: unknown) => request<T>(path, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify(body) });

export const contractsApi = {
  list: (tenant: string) => request<{ contracts: ContractSummary[] }>(`/admin/contracts/list${q(tenant)}`),
  upcoming: (tenant: string) => request<{ deadlines: UpcomingDeadline[] }>(`/admin/contracts/upcoming${q(tenant)}`),
  detail: (tenant: string, id: string) => request<ContractDetail>(`/admin/contracts/${one(id)}${q(tenant)}`),
  importExtraction: (tenant: string, contractId: string, sourceDoc: string, extracted: unknown) =>
    post<ImportResult>("/admin/contracts/import", { tenant_id: tenant, contract_id: contractId, source_doc: sourceDoc, extracted }),
  setField: (tenant: string, id: string, name: string, value: string) =>
    post<ContractSummary>(`/admin/contracts/${one(id)}/field`, { tenant_id: tenant, name, value }),
  confirmField: (tenant: string, id: string, name: string) => post<ContractSummary>(`/admin/contracts/${one(id)}/confirm`, { tenant_id: tenant, name }),
  draft: (tenant: string, id: string, kind: string, mailbox: string) =>
    post<DraftResult>(`/admin/contracts/${one(id)}/draft`, { tenant_id: tenant, kind, mailbox }),
  notify: (tenant: string, dryRun: boolean) => post<NotifyResult>("/admin/contracts/notify", { tenant_id: tenant, dry_run: dryRun }),
};
