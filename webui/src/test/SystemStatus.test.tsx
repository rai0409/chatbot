import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SystemStatusPage } from "../pages/SystemStatus";
import { STATUS, jsonResponse } from "./fixtures";

afterEach(cleanup);

beforeEach(() => {
  document.cookie = "admin_csrf=token-123";
  vi.stubGlobal("location", { ...window.location, assign: vi.fn(), pathname: "/console/system" });
});

describe("the system status page", () => {
  it("shows every integration with its state and the reason in words, and the LLM profiles", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(STATUS)));
    render(<SystemStatusPage />);
    expect(screen.getByRole("status")).toHaveTextContent("読み込み中");
    const row = await screen.findByRole("row", { name: /SharePoint 同期/ });
    expect(within(row).getByText("無効")).toBeInTheDocument();
    expect(within(row).getByText("環境で許可されていません（EXTERNAL_INTEGRATIONS）")).toBeInTheDocument();
    expect(screen.getByText(/障害（抽出型で継続中）/)).toBeInTheDocument();
    expect(screen.getByText("連続して失敗したため一時停止中です")).toBeInTheDocument();
    expect(screen.getByRole("row", { name: /local/ })).toHaveTextContent("停止中");
    expect(screen.getByRole("row", { name: /cloud/ })).toHaveTextContent("外部");
    expect(screen.getByRole("note")).toHaveTextContent("configured but off");
  });

  it("says why there is nothing to show when the status cannot be read, and tries again", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(jsonResponse({ detail: "boom" }, 500)).mockResolvedValue(jsonResponse(STATUS));
    vi.stubGlobal("fetch", fetchMock);
    render(<SystemStatusPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("状態を取得できませんでした");
    fireEvent.click(screen.getByRole("button", { name: "再読み込み" }));
    expect(await screen.findByRole("row", { name: /Slack 通知/ })).toBeInTheDocument();
  });

  it("needs a reason, sends the CSRF token and the revision, and shows the new state", async () => {
    const after = { ...STATUS, revision: 4 };
    const fetchMock = vi.fn().mockResolvedValueOnce(jsonResponse(STATUS)).mockResolvedValueOnce(jsonResponse({ change: {}, ...after }));
    vi.stubGlobal("fetch", fetchMock);
    render(<SystemStatusPage />);
    const row = await screen.findByRole("row", { name: /Slack 通知/ });
    fireEvent.click(within(row).getByRole("button", { name: "オフにする" }));
    const dialog = screen.getByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "実行" });
    expect(confirm).toBeDisabled(); // no reason, no change
    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "  チャンネルの整理  " } });
    fireEvent.click(confirm);
    expect(await screen.findByText("変更しました")).toBeInTheDocument();
    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe("/admin/system/switches");
    expect(JSON.parse(init.body)).toEqual({ name: "slack", enabled: false, reason: "チャンネルの整理", expected_rev: 3 });
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("token-123");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("tells the administrator when someone else changed the settings first", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(jsonResponse(STATUS)).mockResolvedValueOnce(jsonResponse({ detail: "changed" }, 409));
    vi.stubGlobal("fetch", fetchMock);
    render(<SystemStatusPage />);
    const row = await screen.findByRole("row", { name: /SharePoint 同期/ });
    fireEvent.click(within(row).getByRole("button", { name: "オンにする" }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "理由" } });
    fireEvent.click(screen.getByRole("button", { name: "実行" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("他の人が先に設定を変更しました"));
  });

  it("has no switch for the integrations that keep their own (the LLM and sign-on)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(STATUS)));
    render(<SystemStatusPage />);
    const llm = await screen.findByRole("row", { name: /LLM（AI/ });
    expect(within(llm).queryByRole("button")).toBeNull();
    expect(within(llm).getByText(/スイッチがありません/)).toBeInTheDocument();
  });

  it("goes to the login page when the session has ended", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail: "x" }, 401)));
    render(<SystemStatusPage />);
    await waitFor(() => expect(window.location.assign).toHaveBeenCalledWith("/admin/login?next=%2Fconsole%2Fsystem"));
  });
});
