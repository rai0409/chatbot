// Records of questions and answers, and the judgements of them (webapi/routers/records.py).
import { request } from "./client";

export type Verdict = "correct" | "partial" | "incorrect";
export type RecordLevel = "off" | "anonymous" | "standard" | "detailed";
export const LEVELS: RecordLevel[] = ["off", "anonymous", "standard", "detailed"];
export const VERDICTS: Verdict[] = ["correct", "partial", "incorrect"];
export const REVIEW_REASONS = ["wrong_source", "outdated", "hallucination", "missing_info", "too_vague", "other"] as const;
export type ReviewReason = (typeof REVIEW_REASONS)[number];

export interface Citation {
  source_doc?: string;
  page?: number | string;
  chunk_id?: string;
  status?: string;
}

export interface Feedback {
  type?: string | null;
  bad_reason?: string | null;
  comment?: string | null;
  t?: string | null;
}

export interface Review {
  verdict: Verdict;
  reasons: string[];
  note?: string;
  expected_answer?: string;
  reviewer?: string;
  at?: string;
}

export interface RecordRow {
  id: string;
  t: string;
  tenant_id: string;
  level: RecordLevel;
  answer_mode?: string | null;
  guard_reason?: string | null;
  degraded?: boolean;
  citations_count?: number;
  question?: string;
  answer_text?: string;
  citations?: Citation[];
  user?: string | null;
  anonymized?: boolean;
  ip?: string | null;
  user_agent?: string | null;
  feedback: Feedback | null;
  review: Review | null;
}

export interface RecordPage {
  total: number;
  offset: number;
  records: RecordRow[];
}

export interface RecordFilters {
  days: number;
  tenant_id: string;
  answer_mode: string;
  verdict: string;
  feedback: string;
  reviewed: "all" | "yes" | "no";
  abstained: boolean;
  text: string;
}

export const PAGE_SIZE = 50;

export interface LevelOverview {
  default: RecordLevel;
  ceiling: RecordLevel;
  global: RecordLevel;
  effective: RecordLevel;
  tenants: Record<string, { requested: RecordLevel; effective: RecordLevel }>;
}

export interface PurgeResult {
  records: { dry_run: boolean; action: string; files_deleted: number; records_anonymized: number; device_info_removed: number };
  llm_usage: { dry_run?: boolean; files_deleted?: number } & Record<string, unknown>;
}

export interface ReviewBody {
  verdict: Verdict;
  reviewer: string;
  reasons: string[];
  note: string;
  expected_answer: string;
}

const json = { "Content-Type": "application/json" };

export function listQuery(filters: RecordFilters, offset: number): string {
  const q = new URLSearchParams();
  q.set("days", String(filters.days));
  if (filters.tenant_id.trim()) q.set("tenant_id", filters.tenant_id.trim());
  if (filters.answer_mode) q.set("answer_mode", filters.answer_mode);
  if (filters.verdict) q.set("verdict", filters.verdict);
  if (filters.feedback) q.set("feedback", filters.feedback);
  if (filters.reviewed !== "all") q.set("reviewed", filters.reviewed === "yes" ? "true" : "false");
  if (filters.abstained) q.set("abstained", "true");
  if (filters.text.trim()) q.set("text", filters.text.trim());
  q.set("limit", String(PAGE_SIZE));
  q.set("offset", String(offset));
  return q.toString();
}

export const recordsApi = {
  list: (filters: RecordFilters, offset: number) => request<RecordPage>(`/admin/records?${listQuery(filters, offset)}`),
  get: (id: string) => request<RecordRow>(`/admin/records/${encodeURIComponent(id)}`),
  review: (id: string, body: ReviewBody) =>
    request<Review>(`/admin/records/${encodeURIComponent(id)}/review`, { method: "POST", headers: json, body: JSON.stringify(body) }),
  level: () => request<LevelOverview>("/admin/records/level"),
  setLevel: (body: { level: RecordLevel | null; reason: string; tenant_id?: string }) =>
    request<LevelOverview>("/admin/records/level", { method: "POST", headers: json, body: JSON.stringify(body) }),
  purge: (body: { dry_run: boolean; action?: string }) =>
    request<PurgeResult>("/admin/records/purge", { method: "POST", headers: json, body: JSON.stringify(body) }),
};
