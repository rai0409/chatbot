import type { GoldenCase, GoldenList } from "../api/golden";
import { jsonResponse } from "./fixtures";

export const CASE_A: GoldenCase = {
  case_id: "gq-a1",
  question: "有給休暇は何日ですか",
  expected_all: ["10日"],
  expected_any: [],
  forbidden_any: ["不明"],
  reference: "入社6か月後に10日です",
  expected_question_type: "",
  group: "leave",
  tags: ["from-record"],
  expected_citation: true,
  enabled: true,
  priority: 5,
  origin: "record:rec-aaa111",
  updated_at: "2026-10-28T09:00:00+00:00",
};

export const CASE_B: GoldenCase = {
  case_id: "gq-b2",
  question: "経費の精算期限はいつですか",
  expected_any: ["翌月末"],
  enabled: false,
  priority: 0,
  origin: "manual",
  updated_at: "2026-10-27T09:00:00+00:00",
};

export const LIST: GoldenList = { tenant_id: "default", total: 2, enabled: 1, cases: [CASE_A, CASE_B] };

type Handler = (init: RequestInit | undefined, url: string) => Response;

/** A fetch that answers by "METHOD /path"; an unlisted call is a 500 so a test notices it. */
export function router(routes: Record<string, Handler>) {
  return (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const key = `${(init?.method ?? "GET").toUpperCase()} ${url.split("?")[0]}`;
    const handler = routes[key];
    return Promise.resolve(handler ? handler(init, url) : jsonResponse({ detail: `no route ${key}` }, 500));
  };
}
