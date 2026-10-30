// The answer review queue (/admin/review/*). Items come from bad feedback and abstentions; an operator logs an action per item.
import { ApiError, request } from "./client";

export type ReviewPriority = "high" | "medium" | "low";

export interface ReviewItem {
  review_id: string | null;
  priority: string | null;
  status: string | null;
  tenant_id: string | null;
  user_query: string | null;
  answer_mode: string | null;
  confidence_route: string | null;
  decision_route: string | null;
  candidate_ids: string[];
  selected_candidate_id: string | null;
  feedback_type: string | null;
  bad_reason: string | null;
  created_at: string | null;
  source: string | null;
  reasons: string[];
}

export interface ReviewItemsResponse {
  items: ReviewItem[];
  total_loaded: number;
  returned_count: number;
  skipped_malformed_lines: number;
  source_path: string;
}

export interface ReviewFilters {
  priority: string;
  status: string;
  tenant: string;
  limit: string;
}

export interface ReviewActionBody {
  review_id: string;
  action_type: string;
  feedback_token: null;
  tenant_id: string;
  selected_candidate_id: string | null;
  operator_note: string | null;
  status_after: string | null;
  tags: string[];
}

export interface ReviewActionResponse {
  ok: boolean;
  stored: boolean;
  action_id: string;
  review_id: string;
  action_type: string;
  warning?: string;
}

export const ACTION_TYPES = [
  "approve_candidate",
  "reject_candidate",
  "needs_new_faq",
  "needs_document_update",
  "needs_policy_review",
  "mark_resolved",
  "mark_ignored",
] as const;

export const STATUS_AFTER = ["open", "in_review", "resolved", "ignored", "needs_followup"] as const;

export type ReviewLoad = { forbidden: false; payload: ReviewItemsResponse } | { forbidden: true };

export function itemsQuery(filters: ReviewFilters): string {
  const search = new URLSearchParams();
  if (filters.priority) search.set("priority", filters.priority);
  if (filters.status) search.set("status", filters.status);
  if (filters.tenant.trim()) search.set("tenant_id", filters.tenant.trim());
  if (filters.limit) search.set("limit", filters.limit);
  const text = search.toString();
  return text ? `?${text}` : "";
}

/** A 403 (the tenant filter is outside the administrator's tenants) is a normal answer, not a failure. */
export async function loadReviewItems(filters: ReviewFilters): Promise<ReviewLoad> {
  try {
    return { forbidden: false, payload: await request<ReviewItemsResponse>(`/admin/review/items${itemsQuery(filters)}`) };
  } catch (e) {
    if (e instanceof ApiError && e.status === 403) return { forbidden: true };
    throw e;
  }
}

export function postReviewAction(body: ReviewActionBody): Promise<ReviewActionResponse> {
  return request<ReviewActionResponse>("/admin/review/action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
