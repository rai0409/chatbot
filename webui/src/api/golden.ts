// The golden questions of a tenant (webapi/routers/golden.py).
import { request } from "./client";

export interface GoldenCase {
  case_id: string;
  question: string;
  expected_all?: string[];
  expected_any?: string[];
  forbidden_any?: string[];
  expected_question_type?: string;
  group?: string;
  origin_change?: string;
  reference?: string;
  tags?: string[];
  expected_citation?: boolean;
  enabled?: boolean;
  priority?: number;
  origin?: string;
  created_by?: string;
  created_at?: string;
  updated_by?: string;
  updated_at?: string;
}

export interface GoldenList {
  tenant_id: string;
  total: number;
  enabled: number;
  cases: GoldenCase[];
}

/** What the form sends (the server ignores nothing it does not know, so only these fields are sent). */
export interface CaseInput {
  case_id?: string;
  question: string;
  expected_all: string[];
  expected_any: string[];
  forbidden_any: string[];
  reference: string;
  expected_question_type: string;
  group: string;
  tags: string[];
  expected_citation: boolean;
  enabled: boolean;
  priority: number;
  expected_updated_at?: string;
}

const json = { "Content-Type": "application/json" };
const base = (tenant: string) => `/admin/golden/${encodeURIComponent(tenant)}`;

export const goldenApi = {
  list: (tenant: string) => request<GoldenList>(base(tenant)),
  create: (tenant: string, body: CaseInput) => request<GoldenCase>(base(tenant), { method: "POST", headers: json, body: JSON.stringify(body) }),
  update: (tenant: string, id: string, body: Partial<CaseInput>) =>
    request<GoldenCase>(`${base(tenant)}/${encodeURIComponent(id)}`, { method: "PUT", headers: json, body: JSON.stringify(body) }),
  remove: (tenant: string, id: string) => request<{ ok: boolean }>(`${base(tenant)}/${encodeURIComponent(id)}`, { method: "DELETE" }),
  export: (tenant: string) => request<{ tenant_id: string; golden_qa: unknown[] }>(`${base(tenant)}/export`),
  fromRecord: (tenant: string, recordId: string) =>
    request<GoldenCase>(`${base(tenant)}/from-record`, { method: "POST", headers: json, body: JSON.stringify({ record_id: recordId }) }),
};
