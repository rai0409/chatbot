import { vi } from "vitest";
import type { Evaluations, Knob, Monitoring, Proposal, ProposalList, ProposalSummary, ProfileVersions } from "../api/changes";
import { jsonResponse } from "./fixtures";

export const STATES = ["observed", "classified", "proposed", "evaluated", "approved", "shadow", "canary", "live", "monitoring", "retired", "rejected"];

export function summary(over: Partial<ProposalSummary> = {}): ProposalSummary {
  return {
    id: "p-001",
    title: "距離のしきい値を見直す",
    state: "evaluated",
    category: "guard_threshold",
    profile_id: "sample",
    proposed_by: "山田太郎",
    approvals: [],
    approvals_required: 2,
    decision: "pass",
    rev: 4,
    updated_at: "2026-10-01T09:00:00+00:00",
    ...over,
  };
}

export function proposal(over: Partial<Proposal> = {}, sum: Partial<ProposalSummary> = {}): Proposal {
  return {
    id: "p-001",
    title: "距離のしきい値を見直す",
    state: "evaluated",
    category: "guard_threshold",
    profile_id: "sample",
    proposed_by: "山田太郎",
    source: { note: "否定的な評価が増えた" },
    change: { knobs: { RAG_HARD_MAX_DIST: 0.9 } },
    evaluation: { ledger_seq: 2, run_id: "run-7", decision: "pass", eval_set_sha256: "abcdef0123456789abcdef", entry_sha256: "ff" },
    approvals: [],
    history: [
      { at: "2026-09-30T09:00:00+00:00", actor: "山田太郎", from: null, to: "observed", note: "created", evidence: {} },
      { at: "2026-09-30T10:00:00+00:00", actor: "山田太郎", from: "observed", to: "classified", note: "", evidence: { category: "guard_threshold" } },
      { at: "2026-10-01T09:00:00+00:00", actor: "田中太郎", from: "proposed", to: "evaluated", note: "", evidence: { ledger_seq: 2, decision: "pass" } },
    ],
    rev: 4,
    summary: summary(sum),
    ...over,
  };
}

export const LIST: ProposalList = {
  states: STATES,
  proposals: [
    summary(),
    summary({ id: "p-002", title: "シノニムを足す", state: "observed", category: null, proposed_by: null, approvals_required: null, decision: null }),
  ],
};

export const VERSIONS: ProfileVersions = {
  profile_id: "sample",
  active: "aaaa1111bbbb2222",
  versions: [
    { version: "aaaa1111bbbb2222", sha256: "aaaa1111bbbb2222x", created_at: "2026-09-01T00:00:00+00:00", created_by: "佐藤次郎", note: "初版", files: ["synonyms.json"] },
    { version: "cccc3333dddd4444", sha256: "cccc3333dddd4444x", created_at: "2026-09-20T00:00:00+00:00", created_by: "佐藤次郎", note: "", files: ["synonyms.json", "rules.json"] },
  ],
  history: [{ version: "0000111122223333", activated_at: "2026-08-01T00:00:00+00:00", activated_by: "田中太郎", deactivated_at: "2026-09-01T00:00:00+00:00" }],
};

export const KNOBS: Knob[] = [
  { name: "VECTOR_TOP_K", kind: "int", stage: "retrieval", risk: "low", lo: 1, hi: 200, description: "ベクトル検索の候補数", value: 20 },
  { name: "RAG_HARD_MAX_DIST", kind: "float", stage: "guard", risk: "high", lo: 0.3, hi: 1.5, description: "回答しない距離", value: 1.0 },
  { name: "MULTI_QUERY_ENABLED", kind: "bool", stage: "query", risk: "low", lo: null, hi: null, description: "複数の問い合わせ", value: false },
];

export const EVALUATIONS: Evaluations = {
  chain_intact: true,
  total: 2,
  entries: [
    { seq: 2, at: "2026-10-01T08:00:00+00:00", run_id: "run-7", decision: "pass", proposal_id: "p-001", eval_set_sha256: "abcdef0123456789abcdef" },
    { seq: 1, at: "2026-09-30T08:00:00+00:00", run_id: "run-6", decision: "insufficient", proposal_id: "p-009", eval_set_sha256: "1234567890abcdef1234" },
  ],
};

export const MONITORING: Monitoring = {
  days: 14,
  feedback_by_day: { "2026-10-01": { good: 4, bad: 2 }, "2026-10-02": { good: 1, neutral: 3, human_review_requested: 1 } },
  records: { total: 120, abstained: 7, degraded: 3, feedback: { good: 5, bad: 2 }, verdicts: { correct: 4 } },
  changes_in_rollout: [summary({ id: "p-003", title: "ブースト調整", state: "canary" })],
};

type Handler = unknown | ((body: unknown) => Response);

/** A fetch that answers by "METHOD /path?query". Anything not listed is a 404. The calls are on the returned mock. */
export function stubApi(routes: Record<string, Handler>) {
  const mock = vi.fn((url: string, init?: RequestInit) => {
    const key = `${(init?.method ?? "GET").toUpperCase()} ${url}`;
    if (!(key in routes)) return Promise.resolve(jsonResponse({ detail: `no route ${key}` }, 404));
    const handler = routes[key];
    if (typeof handler === "function") return Promise.resolve((handler as (b: unknown) => Response)(init?.body ? JSON.parse(String(init.body)) : undefined));
    return Promise.resolve(jsonResponse(handler));
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}

/** The posts made so far: [path, body, headers]. */
export function posts(mock: ReturnType<typeof stubApi>) {
  return mock.mock.calls
    .filter(([, init]) => (init?.method ?? "GET") === "POST")
    .map(([url, init]) => ({ url, body: JSON.parse(String(init?.body)), csrf: new Headers(init?.headers).get("X-CSRF-Token") }));
}
