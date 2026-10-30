// Synonym candidate review (/admin/synonyms/*). Only the installation administrator may use it (403 for others).
import { ApiError, request } from "./client";

export type CandidateStatus = "candidate" | "approved" | "rejected";

export interface SynonymCandidate {
  candidate_id: string;
  method: string | null;
  canonical: string | null;
  synonyms: string[];
  status: string;
  decided_by: string | null;
  decided_at: string | null;
}

export interface CandidateList {
  items: SynonymCandidate[];
  counts: Record<string, number>;
  total: number;
}

export interface ApplyResult {
  profile_id: string;
  pinned_version: string | null;
  approved_rows: number;
  entries_before: number;
  entries_after: number;
}

export type CandidateLoad = { forbidden: false; list: CandidateList } | { forbidden: true };

/** The optional administrator token of the legacy page (not needed when signed in). Never logged. */
function headers(token: string, json: boolean): Headers {
  const h = new Headers();
  if (json) h.set("Content-Type", "application/json");
  if (token) h.set("X-Admin-Token", token);
  return h;
}

export async function loadCandidates(status: string, token: string): Promise<CandidateLoad> {
  try {
    const query = status ? `?status=${encodeURIComponent(status)}` : "";
    return { forbidden: false, list: await request<CandidateList>(`/admin/synonyms/candidates${query}`, { headers: headers(token, false) }) };
  } catch (e) {
    if (e instanceof ApiError && e.status === 403) return { forbidden: true };
    throw e;
  }
}

export function decideCandidate(candidateId: string, status: CandidateStatus, token: string) {
  return request<{ candidate_id: string; status: string }>("/admin/synonyms/decision", {
    method: "POST",
    headers: headers(token, true),
    body: JSON.stringify({ candidate_id: candidateId, status }),
  });
}

export function applyApproved(profileId: string, token: string) {
  return request<ApplyResult>("/admin/synonyms/apply", {
    method: "POST",
    headers: headers(token, true),
    body: JSON.stringify({ profile_id: profileId }),
  });
}
