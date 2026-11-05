import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, csrfToken, request } from "../api/client";
import { jsonResponse } from "./fixtures";

afterEach(() => vi.unstubAllGlobals());

describe("the API client", () => {
  it("reads the CSRF token from the cookie and sends it only with a change", async () => {
    document.cookie = "admin_csrf=abc%20def";
    expect(csrfToken()).toBe("abc def");
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse({ ok: true }))); // a body is read once
    vi.stubGlobal("fetch", fetchMock);
    await request("/admin/x");
    await request("/admin/x", { method: "POST" });
    expect(new Headers(fetchMock.mock.calls[0][1].headers).has("X-CSRF-Token")).toBe(false);
    expect(new Headers(fetchMock.mock.calls[1][1].headers).get("X-CSRF-Token")).toBe("abc def");
    expect(fetchMock.mock.calls[0][1].credentials).toBe("same-origin");
  });

  it("turns an error answer into an ApiError with the server's detail", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail: "integration_off:switch_off" }, 409)));
    await expect(request("/admin/x")).rejects.toMatchObject({ status: 409, detail: "integration_off:switch_off" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("not json", { status: 502, statusText: "Bad Gateway" })));
    await expect(request("/admin/x")).rejects.toBeInstanceOf(ApiError);
  });
});
