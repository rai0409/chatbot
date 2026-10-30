// Change management: the shapes of webapi/routers/governance.py and the calls to it.
import { request } from "./client";

export type KnobValue = boolean | number;
export type MoveName = "classify" | "propose" | "attach-evaluation" | "approve" | "reject" | "advance" | "retire";

export interface ProposalSummary {
  id: string;
  title: string;
  state: string;
  category: string | null;
  profile_id: string;
  proposed_by: string | null;
  approvals: string[];
  approvals_required: number | null;
  decision: string | null;
  rev: number;
  updated_at: string | null;
}

export interface ProposalList {
  states: string[];
  proposals: ProposalSummary[];
}

export interface HistoryItem {
  at: string;
  actor: string;
  from: string | null;
  to: string;
  note: string;
  evidence: Record<string, unknown>;
}

export interface ProposalChange {
  profile_version?: string;
  knobs?: Record<string, KnobValue>;
  manual?: string;
}

export interface ProposalEvaluation {
  ledger_seq: number;
  run_id: string | null;
  decision: string | null;
  eval_set_sha256: string | null;
  entry_sha256: string | null;
}

export interface Approval {
  actor: string;
  at: string;
  note: string;
  accepted_insufficient: string | null;
}

export interface Proposal {
  id: string;
  title: string;
  state: string;
  category: string | null;
  profile_id: string;
  proposed_by: string | null;
  source: Record<string, unknown>;
  change: ProposalChange | null;
  evaluation: ProposalEvaluation | null;
  approvals: Approval[];
  history: HistoryItem[];
  rev: number;
  summary: ProposalSummary;
}

export interface ProfileRow {
  profile_id: string;
  versions: number;
  active: string | null;
}

export interface ProfileVersion {
  version: string;
  sha256?: string;
  created_at?: string;
  created_by?: string;
  note?: string;
  files?: string[];
  error?: string;
}

export interface PointerEntry {
  version: string;
  activated_at: string;
  activated_by: string;
  deactivated_at?: string;
}

export interface ProfileVersions {
  profile_id: string;
  active: string | null;
  versions: ProfileVersion[];
  history: PointerEntry[];
}

export interface ProfileDiff {
  left: string;
  right: string;
  files: Record<string, "same" | "changed" | "added" | "removed">;
  identical: boolean;
}

export interface LedgerEntry {
  seq: number;
  at?: string;
  run_id?: string;
  decision?: string;
  proposal_id?: string | null;
  eval_set_sha256?: string;
}

export interface Evaluations {
  chain_intact: boolean;
  total: number;
  entries: LedgerEntry[];
}

export interface Knob {
  name: string;
  kind: "bool" | "int" | "float";
  stage: string;
  risk: string;
  lo: number | null;
  hi: number | null;
  description: string;
  value: KnobValue | null;
}

export interface Monitoring {
  days: number;
  feedback_by_day: Record<string, Record<string, number>>;
  records: {
    total: number;
    abstained: number;
    degraded: number;
    feedback: Record<string, number>;
    verdicts: Record<string, number>;
  };
  changes_in_rollout: ProposalSummary[];
}

export interface MoveBody {
  actor: string;
  note?: string;
  category?: string;
  change?: ProposalChange;
  ledger_seq?: number;
  accept_insufficient_reason?: string;
  reason?: string;
  evidence?: string;
}

const JSON_HEADERS = { "Content-Type": "application/json" };
const post = <T>(path: string, body: unknown) => request<T>(path, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify(body) });
const enc = encodeURIComponent;

export const changesApi = {
  proposals: (state: string) => request<ProposalList>(`/admin/proposals${state ? `?state=${enc(state)}` : ""}`),
  proposal: (id: string) => request<Proposal>(`/admin/proposals/${enc(id)}`),
  createProposal: (body: { id: string; profile_id: string; title: string; actor: string; source?: Record<string, unknown> }) =>
    post<Proposal>("/admin/proposals", body),
  move: (id: string, move: MoveName, body: MoveBody) => post<Proposal>(`/admin/proposals/${enc(id)}/${move}`, body),
  profiles: () => request<{ profiles: ProfileRow[] }>("/admin/profiles"),
  versions: (id: string) => request<ProfileVersions>(`/admin/profiles/${enc(id)}/versions`),
  diff: (id: string, left: string, right: string) =>
    request<ProfileDiff>(`/admin/profiles/${enc(id)}/diff?left=${enc(left)}&right=${enc(right)}`),
  activate: (id: string, body: { version: string; actor: string; reason: string }) => post<unknown>(`/admin/profiles/${enc(id)}/activate`, body),
  rollback: (id: string, body: { actor: string; reason: string }) => post<unknown>(`/admin/profiles/${enc(id)}/rollback`, body),
  evaluations: (limit = 200) => request<Evaluations>(`/admin/evaluations?limit=${limit}`),
  knobs: () => request<{ knobs: Knob[] }>("/admin/knobs"),
  monitoring: (days: number) => request<Monitoring>(`/admin/monitoring?days=${days}`),
};
