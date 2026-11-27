// Calls to the web API. The browser carries the administrator's session cookie; a request that changes something also
// carries the CSRF token that the server put in the `admin_csrf` cookie (the same rule as the older admin pages).
import { formatDetail } from "../lib/errors";
import type { ApprovalDecision, ApprovalItem, SwitchRequest, SwitchResponse, SystemStatus } from "./types";

/** A failed request. `detail` is readable text (a validation list is already in words); `body` is the server's own
 *  `detail` value. Status 0 means the server was not reached. */
export class ApiError extends Error {
  constructor(public status: number, public detail: string, public body?: unknown) {
    super(`${status}: ${detail}`);
  }
}

/** The ApiError for an answer that is not ok (the body is read once). */
export async function errorFrom(response: Response): Promise<ApiError> {
  let detail = response.statusText;
  let raw: unknown;
  try {
    const body = await response.json();
    raw = body?.detail;
    detail = formatDetail(raw) || detail;
  } catch {
    /* the body is not JSON: keep the status text */
  }
  return new ApiError(response.status, detail, raw);
}

/** fetch, with a failure to reach the server turned into ApiError(0) so that every screen says the same thing. */
export async function send(path: string, init: RequestInit): Promise<Response> {
  try {
    return await fetch(path, init);
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError(0, "network");
  }
}

export const LOGIN_URL = "/admin/login";

export function csrfToken(): string {
  const found = document.cookie.match(/(?:^|;\s*)admin_csrf=([^;]+)/);
  return found ? decodeURIComponent(found[1]) : "";
}

function goToLogin(): void {
  window.location.assign(`${LOGIN_URL}?next=${encodeURIComponent(window.location.pathname)}`);
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (method !== "GET" && method !== "HEAD") headers.set("X-CSRF-Token", csrfToken());
  const response = await send(path, { ...init, headers, credentials: "same-origin" });
  if (response.status === 401) {
    goToLogin();
    throw new ApiError(401, "unauthorized");
  }
  if (!response.ok) throw await errorFrom(response);
  return (await response.json()) as T;
}

export const api = {
  systemStatus: () => request<SystemStatus>("/admin/system/status"),
  setSwitch: (body: SwitchRequest) =>
    request<SwitchResponse>("/admin/system/switches", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  approvals: (status: string) =>
    request<{ required: number; require_sso: boolean; approvals: ApprovalItem[] }>(`/admin/system/approvals?status=${status}`),
  decideApproval: (id: string, decision: ApprovalDecision, actor: string) =>
    request<{ approval: ApprovalItem } & SystemStatus>(`/admin/system/approvals/${encodeURIComponent(id)}/${decision}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ actor, reason: "" }),
    }),
};
