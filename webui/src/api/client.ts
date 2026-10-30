// Calls to the web API. The browser carries the administrator's session cookie; a request that changes something also
// carries the CSRF token that the server put in the `admin_csrf` cookie (the same rule as the older admin pages).
import type { ApprovalDecision, ApprovalItem, SwitchRequest, SwitchResponse, SystemStatus } from "./types";

export class ApiError extends Error {
  constructor(public status: number, public detail: string) {
    super(`${status}: ${detail}`);
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
  const response = await fetch(path, { ...init, headers, credentials: "same-origin" });
  if (response.status === 401) {
    goToLogin();
    throw new ApiError(401, "unauthorized");
  }
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* the body is not JSON: keep the status text */
    }
    throw new ApiError(response.status, detail);
  }
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
