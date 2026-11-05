import type { ContractDetail, ContractSummary, NotifyResult, UpcomingDeadline } from "../api/contracts";
import { jsonResponse } from "./fixtures";

const empty = { value: null, state: "missing", confidence: null, evidence: null, updated_by: null, updated_at: null } as const;

export const SUMMARIES: ContractSummary[] = [
  {
    contract_id: "service-2026-01",
    counterparty: "株式会社サンプル",
    title: "保守契約",
    status: "needs_review",
    source_doc: "keiyaku.pdf",
    nearest_deadline: { kind: "end", label: "契約の終了日", date: "2026-12-31", days_left: 63, basis: ["end_date"], verified: false },
    unconfirmed: 2,
  },
  { contract_id: "old-2020", counterparty: null, title: null, status: "expired", source_doc: "", nearest_deadline: null, unconfirmed: 0 },
];

export const UPCOMING: UpcomingDeadline[] = [
  { kind: "end", label: "契約の終了日", date: "2026-12-31", days_left: 63, basis: ["end_date"], verified: false, contract_id: "service-2026-01", counterparty: "株式会社サンプル", title: "保守契約", status: "needs_review" },
  { kind: "termination_notice", label: "解約の通知期限", date: "2026-10-01", days_left: -28, basis: [], verified: true, contract_id: "old-2020", counterparty: null, title: null, status: "expired" },
];

export const DETAIL: ContractDetail = {
  contract_id: "service-2026-01",
  source_doc: "keiyaku.pdf",
  status: "needs_review",
  required: ["counterparty", "end_date"],
  fields: {
    counterparty: { value: "株式会社サンプル", state: "extracted", confidence: 0.9, evidence: { page: 1, text: "甲: 株式会社サンプル" }, updated_by: "x", updated_at: null },
    title: { value: "保守契約", state: "confirmed", confidence: null, evidence: null, updated_by: "x", updated_at: null },
    end_date: { value: "2026-12-31", state: "extracted", confidence: 0.8, evidence: { page: 3, text: "令和8年12月31日まで" }, updated_by: "x", updated_at: null },
    contact_email: { ...empty },
  },
  deadlines: [{ kind: "end", label: "契約の終了日", date: "2026-12-31", days_left: 63, basis: ["end_date"], verified: false }],
  history: [{ at: "2026-10-01T00:00:00+00:00", by: "admin", action: "extracted", field: "end_date" }],
};

export const NOTIFY_DRY: NotifyResult = {
  due: 1,
  sent: 0,
  failed: 0,
  dry_run: true,
  results: [{ key: "k1", contract_id: "service-2026-01", kind: "end", date: "2026-12-31", offset: 90, verified: false, status: "due" }],
};

type Handler = (url: string, init?: RequestInit) => Response | undefined;

/** A fetch that answers by route; `overrides` are tried first, so a test can change one answer. */
export function contractsFetch(overrides: Handler[] = []) {
  return (url: string, init?: RequestInit): Promise<Response> => {
    for (const h of overrides) {
      const r = h(url, init);
      if (r) return Promise.resolve(r);
    }
    const method = (init?.method ?? "GET").toUpperCase();
    if (method === "GET" && url.startsWith("/admin/contracts/list")) return Promise.resolve(jsonResponse({ contracts: SUMMARIES }));
    if (method === "GET" && url.startsWith("/admin/contracts/upcoming")) return Promise.resolve(jsonResponse({ deadlines: UPCOMING }));
    if (method === "GET" && url.startsWith("/admin/contracts/service-2026-01")) return Promise.resolve(jsonResponse(DETAIL));
    if (url.endsWith("/notify")) return Promise.resolve(jsonResponse(NOTIFY_DRY));
    if (method === "POST") return Promise.resolve(jsonResponse(SUMMARIES[0]));
    return Promise.resolve(jsonResponse({ detail: "unexpected " + url }, 500));
  };
}
