import type { LevelOverview, PurgeResult, RecordPage, RecordRow } from "../api/records";
import { jsonResponse } from "./fixtures";

export const REC_TEXT: RecordRow = {
  id: "rec-aaa111",
  t: "2026-10-28T09:30:00+00:00",
  tenant_id: "default",
  level: "standard",
  answer_mode: "rag_answer",
  guard_reason: null,
  degraded: true,
  citations_count: 1,
  question: "有給休暇は何日ありますか。山田太郎さんの場合を教えてください。",
  answer_text: "入社6か月後に10日付与されます。",
  citations: [{ source_doc: "就業規則.pdf", page: 12, chunk_id: "c1" }],
  feedback: { type: "bad", bad_reason: "wrong_source", comment: "古い規則です" },
  review: null,
};

export const REC_ANON: RecordRow = {
  id: "rec-bbb222",
  t: "2026-10-27T09:30:00+00:00",
  tenant_id: "sample-tenant",
  level: "anonymous",
  answer_mode: "fallback_no_answer",
  guard_reason: "no_evidence",
  degraded: false,
  citations_count: 0,
  feedback: null,
  review: null,
};

export const REC_REVIEWED: RecordRow = {
  ...REC_TEXT,
  review: { verdict: "partial", reasons: ["outdated"], note: "古い", expected_answer: "年に10日です", reviewer: "田中太郎", at: "2026-10-28T10:00:00+00:00" },
};

export const PAGE: RecordPage = { total: 2, offset: 0, records: [REC_TEXT, REC_ANON] };

export const LEVEL: LevelOverview = {
  default: "off",
  ceiling: "standard",
  global: "anonymous",
  effective: "anonymous",
  tenants: { "sample-tenant": { requested: "standard", effective: "standard" } },
};

export const DRY: PurgeResult = {
  records: { dry_run: true, action: "anonymize", files_deleted: 0, records_anonymized: 12, device_info_removed: 3 },
  llm_usage: { dry_run: true, files_deleted: 4 },
};

export const REAL: PurgeResult = { ...DRY, records: { ...DRY.records, dry_run: false, records_anonymized: 12 } };

export interface Sent {
  url: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  body: any;
  token: string | null;
}

/** The calls that change something (POST and others), with the JSON body read and the CSRF header. */
export function sent(mock: { mock: { calls: unknown[][] } }): Sent[] {
  return mock.mock.calls
    .filter((call) => ((call[1] as RequestInit | undefined)?.method ?? "GET") !== "GET")
    .map((call) => {
      const init = call[1] as RequestInit;
      return { url: String(call[0]), body: JSON.parse(String(init.body)), token: new Headers(init.headers).get("X-CSRF-Token") };
    });
}

type Handler =(init: RequestInit | undefined, url: string) => Response;

/** A fetch that answers by "METHOD /path" (the path without its query); an unlisted call is a 500 so a test notices it. */
export function router(routes: Record<string, Handler>) {
  return (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const key = `${(init?.method ?? "GET").toUpperCase()} ${url.split("?")[0]}`;
    const handler = routes[key];
    return Promise.resolve(handler ? handler(init, url) : jsonResponse({ detail: `no route ${key}` }, 500));
  };
}
