// The approved-QA screen: webapi/routers/approved_qa.py (rules in rag_core/approved_qa_store.py).
import { request } from "./client";

export type QaStatus = "draft" | "approved" | "rejected";

export interface QaCitation {
  source_doc: string;
  source_pages: number[];
  title?: string;
}

export interface QaRow {
  qa_id: string;
  tenant_id: string;
  question: string;
  approved_answer: string;
  status: QaStatus;
  tags: string[];
  aliases: string[];
  citations: number;
  updated_at: string | null;
  updated_by: string | null;
  approved_by: string | null;
}

export interface QaList {
  total: number;
  counts: Record<QaStatus, number>;
  rows: QaRow[];
  /** Whether chat uses the approved-QA file (the APPROVED_QA_ENABLED setting). */
  enabled: boolean;
  require_approver: boolean;
  can_see_all: boolean;
}

export interface QaHealth {
  total: number;
  by_status: Record<QaStatus, number>;
  errors: string[];
  unreadable_lines: number;
  enabled: boolean;
  /** APPROVED_QA_PATH is not set: the console refuses to write until it points to a data file. */
  path_is_default?: boolean;
  /** A tenant administrator sees the counts only (error texts quote other tenants' questions). */
  detail_hidden: boolean;
}

export interface QaRecord {
  qa_id: string;
  tenant_id: string;
  question: string;
  approved_answer: string;
  status: QaStatus;
  tags?: string[];
  approved_aliases?: string[];
  proposed_aliases?: string[];
  approved_citations?: QaCitation[];
  doc_version?: string | null;
  updated_at: string;
  updated_by?: string;
  approved_by?: string;
  /** Present when an edit took an approved record back to draft. */
  reverted_to_draft?: boolean;
}

export interface QaHistoryRow {
  at: string;
  action: string;
  by: string;
  reason: string;
  status_before: QaStatus | null;
  status_after: QaStatus | null;
}

export interface QaFilter {
  tenant: string;
  status: string;
  query: string;
  offset: number;
}

export interface QaSave {
  tenant_id: string;
  qa_id?: string;
  question: string;
  approved_answer: string;
  aliases: string[];
  tags: string[];
  citations: QaCitation[];
  doc_version?: string | null;
  reason: string;
  actor: string;
  expected_updated_at?: string;
}

export interface ImportRowResult {
  line: number;
  action: "create" | "update" | "skip" | "error";
  message: string;
  question: string;
}

export interface ImportResult {
  applied: boolean;
  counts: Record<"create" | "update" | "skip" | "error", number>;
  rows: ImportRowResult[];
}

const JSON_HEADERS = { "Content-Type": "application/json" };
const post = <T>(path: string, body: unknown) => request<T>(path, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify(body) });

export const PAGE_SIZE = 25;

export const approvedQaApi = {
  list: (f: QaFilter) => {
    const q = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(f.offset) });
    if (f.tenant.trim()) q.set("tenant_id", f.tenant.trim());
    if (f.status) q.set("status", f.status);
    if (f.query.trim()) q.set("q", f.query.trim());
    return request<QaList>(`/admin/approved-qa?${q}`);
  },
  health: () => request<QaHealth>("/admin/approved-qa/health"),
  record: (tenant: string, qaId: string) =>
    request<{ record: QaRecord; history: QaHistoryRow[] }>(`/admin/approved-qa/record?${new URLSearchParams({ tenant_id: tenant, qa_id: qaId })}`),
  save: (body: QaSave) => post<QaRecord>("/admin/approved-qa/save", body),
  setStatus: (body: { tenant_id: string; qa_id: string; status: QaStatus; reason: string; actor: string }) =>
    post<QaRecord>("/admin/approved-qa/status", body),
  remove: (body: { tenant_id: string; qa_id: string; reason: string; actor: string }) => post<{ ok: boolean }>("/admin/approved-qa/delete", body),
  importCsv: (body: { tenant_id: string; content_base64: string; apply: boolean; on_conflict: string; reason?: string; actor?: string }) =>
    post<ImportResult>("/admin/approved-qa/import", body),
  /** Plain links: the browser downloads them with the session cookie. */
  templateUrl: "/admin/approved-qa/template",
  exportUrl: (tenant: string, status: string) => {
    const q = new URLSearchParams();
    if (tenant.trim()) q.set("tenant_id", tenant.trim());
    if (status) q.set("status", status);
    return `/admin/approved-qa/export${q.toString() ? `?${q}` : ""}`;
  },
};
