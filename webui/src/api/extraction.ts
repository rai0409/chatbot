// OCR extraction review: the routes of "Extraction review (6-3)" in webapi/main.py.
import { ApiError, LOGIN_URL, request } from "./client";

export type LineState = "open" | "corrected" | "confirmed";

export interface ExtractionDoc {
  source_doc: string;
  saved_at: string;
  pages: number;
  lines: number;
  open: number;
  corrected: number;
  confirmed: number;
  preview_available: boolean;
}

export interface ReviewItem {
  source_doc: string;
  page: number;
  line_id: string;
  text: string;
  confidence: number | null;
  state: LineState;
}

export interface PageLine {
  id: string;
  text: string;
  confidence: number | null;
  /** [left, top, right, bottom] in the pixels of the page image, when the OCR gave a position. */
  bbox?: number[] | null;
  state: LineState;
}

export interface PageData {
  source_doc: string;
  page: number;
  width_px: number;
  height_px: number;
  confidence: number | null;
  preview_available: boolean;
  threshold: number;
  lines: PageLine[];
}

export interface ItemFilter {
  tenant: string;
  maxConfidence: string;
  includeReviewed: boolean;
  sourceDoc: string;
}

export interface LineRef {
  source_doc: string;
  page: number;
  line_id: string;
}

export interface ReingestResult {
  sources: string[];
  note: string;
}

const JSON_HEADERS = { "Content-Type": "application/json" };

function post<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify(body) });
}

export const extractionApi = {
  docs: (tenant: string) =>
    request<{ documents: ExtractionDoc[] }>(`/admin/extraction/docs?${new URLSearchParams({ tenant_id: tenant })}`).then((r) => r.documents),

  items: (f: ItemFilter) => {
    const q = new URLSearchParams({ tenant_id: f.tenant });
    if (f.maxConfidence.trim()) q.set("max_confidence", f.maxConfidence.trim());
    if (f.includeReviewed) q.set("include_reviewed", "true");
    if (f.sourceDoc) q.set("source_doc", f.sourceDoc);
    return request<{ items: ReviewItem[] }>(`/admin/extraction/items?${q}`).then((r) => r.items);
  },

  page: (tenant: string, sourceDoc: string, page: number) =>
    request<PageData>(`/admin/extraction/page?${new URLSearchParams({ tenant_id: tenant, source_doc: sourceDoc, page: String(page) })}`),

  /** The page image is a PNG: load it with the session cookie and return the bytes (the caller makes an object URL). */
  async pageImage(tenant: string, sourceDoc: string, page: number): Promise<Blob> {
    const q = new URLSearchParams({ tenant_id: tenant, source_doc: sourceDoc, page: String(page) });
    const response = await fetch(`/admin/extraction/page-image?${q}`, { credentials: "same-origin" });
    if (response.status === 401) {
      window.location.assign(`${LOGIN_URL}?next=${encodeURIComponent(window.location.pathname)}`);
      throw new ApiError(401, "unauthorized");
    }
    if (!response.ok) throw new ApiError(response.status, response.statusText);
    return response.blob();
  },

  correct: (tenant: string, ref: LineRef, text: string) => post<unknown>("/admin/extraction/correct", { tenant_id: tenant, ...ref, text }),
  confirm: (tenant: string, ref: LineRef) => post<unknown>("/admin/extraction/confirm", { tenant_id: tenant, ...ref }),
  revert: (tenant: string, ref: LineRef) => post<{ reverted: boolean }>("/admin/extraction/revert", { tenant_id: tenant, ...ref }),
  reingest: (tenant: string, sourceDoc: string) =>
    post<ReingestResult>("/admin/extraction/reingest", { tenant_id: tenant, source_doc: sourceDoc }),
};
