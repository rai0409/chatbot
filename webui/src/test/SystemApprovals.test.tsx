import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SystemStatusPage } from "../pages/SystemStatus";
import type { ApprovalItem } from "../api/types";
import { STATUS, jsonResponse } from "./fixtures";
import { routeFetch } from "./overviewFixtures";

afterEach(cleanup);

beforeEach(() => {
  document.cookie = "admin_csrf=token-123";
  vi.stubGlobal("location", { ...window.location, assign: vi.fn(), pathname: "/console/system" });
});

const TWO = { ...STATUS, approvals: { required: 2, pending: 0, require_sso: false } };

const WAITING: ApprovalItem = {
  id: "apr-000000000001",
  kind: "integration_enable",
  params: { name: "slack", tenant_id: null },
  reason: "チャンネルの開設",
  requested_by: { name: "山田太郎", id: null, verified: false },
  requested_at: "2026-10-29T03:00:00+00:00",
  expires_at: "2026-11-01T03:00:00+00:00",
  status: "pending",
  decided_by: null,
  decided_at: null,
};

describe("turning an integration on needs a second person", () => {
  it("asks for the name of the person who asks, sends it, and says that the request waits", async () => {
    const waiting = { ...TWO, approvals: { required: 2, pending: 1, require_sso: false } };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(TWO))
      .mockResolvedValueOnce(jsonResponse({ pending: WAITING, ...waiting }, 202))
      .mockResolvedValueOnce(jsonResponse({ required: 2, require_sso: false, approvals: [WAITING] }));
    vi.stubGlobal("fetch", fetchMock);
    render(<SystemStatusPage />);
    const row = await screen.findByRole("row", { name: /SharePoint 同期/ });
    fireEvent.click(within(row).getByRole("button", { name: "オンにする" }));
    const dialog = screen.getByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "実行" });
    fireEvent.change(within(dialog).getByLabelText("変更の理由（必須）"), { target: { value: "チャンネルの開設" } });
    expect(confirm).toBeDisabled(); // a reason is not enough: the name is needed too
    fireEvent.change(within(dialog).getByLabelText("あなたの名前（必須）"), { target: { value: " 山田太郎 " } });
    fireEvent.click(confirm);
    expect(await screen.findByText(/オンにする依頼を出しました/)).toBeInTheDocument();
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({
      name: "sharepoint", enabled: true, reason: "チャンネルの開設", expected_rev: 3, actor: "山田太郎",
    });
    expect(await screen.findByRole("row", { name: /山田太郎.*チャンネルの開設/ })).toBeInTheDocument();
    expect(String(fetchMock.mock.calls[2][0])).toBe("/admin/system/approvals?status=pending");
  });

  it("does not ask for a name when turning off, and asks for none when one person is enough", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(TWO)));
    const { unmount } = render(<SystemStatusPage />);
    const row = await screen.findByRole("row", { name: /Slack 通知/ });
    fireEvent.click(within(row).getByRole("button", { name: "オフにする" }));
    expect(screen.queryByLabelText("あなたの名前（必須）")).not.toBeInTheDocument();
    unmount();
    cleanup();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ ...STATUS, approvals: { required: 1, pending: 0, require_sso: false } })));
    render(<SystemStatusPage />);
    fireEvent.click(within(await screen.findByRole("row", { name: /SharePoint 同期/ })).getByRole("button", { name: "オンにする" }));
    expect(screen.queryByLabelText("あなたの名前（必須）")).not.toBeInTheDocument();
  });

  it("lists the waiting requests and approves one with the name of the second person", async () => {
    const waiting = { ...TWO, approvals: { required: 2, pending: 1, require_sso: false } };
    const done = { approval: { ...WAITING, status: "approved" }, ...TWO, revision: 4 };
    const fetchMock = routeFetch({ "/admin/system/status": waiting, "/admin/system/approvals?": { required: 2, require_sso: false, approvals: [WAITING] } });
    const calls = fetchMock;
    vi.stubGlobal("fetch", (url: string, init?: RequestInit) =>
      String(url).includes("/approve") ? Promise.resolve(jsonResponse(done)) : calls(url, init));
    render(<SystemStatusPage />);
    const row = await screen.findByRole("row", { name: /山田太郎/ });
    expect(within(row).getByRole("button", { name: "承認" })).toBeDisabled(); // no name, no decision
    fireEvent.change(screen.getByLabelText("操作する人の名前"), { target: { value: "田中太郎" } });
    fireEvent.click(within(row).getByRole("button", { name: "承認" }));
    expect(await screen.findByText("承認しました。連携をオンにしました。")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("row", { name: /山田太郎/ })).not.toBeInTheDocument());
  });

  it("shows the sentence of the server when the decision is refused", async () => {
    const waiting = { ...TWO, approvals: { required: 2, pending: 1, require_sso: false } };
    const fetchMock = routeFetch({ "/admin/system/status": waiting, "/admin/system/approvals?": { required: 2, require_sso: false, approvals: [WAITING] } });
    vi.stubGlobal("fetch", (url: string, init?: RequestInit) =>
      String(url).includes("/approve")
        ? Promise.resolve(jsonResponse({ detail: "the person who asked cannot approve or reject their own request (cancel it instead)" }, 400))
        : fetchMock(url, init));
    render(<SystemStatusPage />);
    const row = await screen.findByRole("row", { name: /山田太郎/ });
    fireEvent.change(screen.getByLabelText("操作する人の名前"), { target: { value: "山田太郎" } });
    fireEvent.click(within(row).getByRole("button", { name: "承認" }));
    expect(await screen.findByText(/操作できませんでした: the person who asked cannot approve/)).toBeInTheDocument();
    expect(screen.getByRole("row", { name: /山田太郎/ })).toBeInTheDocument(); // still waiting
  });
});
