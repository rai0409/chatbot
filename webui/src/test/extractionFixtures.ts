import { vi } from "vitest";
import type { ExtractionDoc, PageData, ReviewItem } from "../api/extraction";
import { jsonResponse } from "./fixtures";

export const DOCS: ExtractionDoc[] = [
  { source_doc: "scan-a.pdf", saved_at: "2026-01-05T09:00:00+00:00", pages: 2, lines: 10, open: 2, corrected: 1, confirmed: 0, preview_available: true },
];

export const ITEMS: ReviewItem[] = [
  { source_doc: "scan-a.pdf", page: 1, line_id: "l1", text: "請求書番号 12O45", confidence: null, state: "open" },
  { source_doc: "scan-a.pdf", page: 1, line_id: "l2", text: "株式会社サンプル", confidence: 61.5, state: "corrected" },
];

export const PAGE: PageData = {
  source_doc: "scan-a.pdf",
  page: 1,
  width_px: 1000,
  height_px: 2000,
  confidence: 70,
  preview_available: true,
  threshold: 80,
  lines: [
    { id: "l1", text: "請求書番号 12O45", confidence: null, bbox: [100, 200, 600, 260], state: "open" },
    { id: "l2", text: "株式会社サンプル", confidence: 61.5, bbox: [100, 300, 500, 360], state: "corrected" },
    { id: "l3", text: "合計 1,000円", confidence: 95, bbox: null, state: "open" },
  ],
};

type Handler = (url: string, init?: RequestInit) => Response;

/** A fetch that answers by "METHOD /path" (the query string is ignored). An unknown route fails the test loudly. */
export function routeFetch(handlers: Record<string, Handler>) {
  const mock = vi.fn((url: string, init?: RequestInit) => {
    const key = `${(init?.method ?? "GET").toUpperCase()} ${String(url).split("?")[0]}`;
    const handler = handlers[key];
    if (!handler) return Promise.reject(new Error(`no route for ${key}`));
    return Promise.resolve(handler(String(url), init));
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}

export function defaultRoutes(extra: Record<string, Handler> = {}): Record<string, Handler> {
  return {
    "GET /admin/extraction/docs": () => jsonResponse({ documents: DOCS }),
    "GET /admin/extraction/items": () => jsonResponse({ items: ITEMS }),
    "GET /admin/extraction/page": () => jsonResponse(PAGE),
    "GET /admin/extraction/page-image": () => new Response(new Uint8Array([0x89, 0x50, 0x4e, 0x47]), { status: 200, headers: { "Content-Type": "image/png" } }),
    ...extra,
  };
}

export function callsTo(mock: ReturnType<typeof routeFetch>, key: string) {
  return mock.mock.calls.filter(([url, init]) => `${(init?.method ?? "GET").toUpperCase()} ${String(url).split("?")[0]}` === key);
}
