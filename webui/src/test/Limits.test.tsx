import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LimitsPage } from "../pages/Limits";
import { EMPTY_LIMITS, LIMITS, jsonResponse } from "./limitsFixtures";

afterEach(cleanup);

beforeEach(() => {
  document.cookie = "admin_csrf=token-123";
  vi.stubGlobal("location", { ...window.location, assign: vi.fn(), pathname: "/console/limits" });
});

function serve(snapshot: unknown = LIMITS, post?: () => Response) {
  const fetchMock = vi.fn().mockImplementation((_url: string, init?: RequestInit) =>
    Promise.resolve(init?.method === "POST" && post ? post() : jsonResponse(snapshot)),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function lastPost(fetchMock: ReturnType<typeof vi.fn>) {
  const call = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
  return call as [string, RequestInit];
}

describe("the limits tab", () => {
  it("shows the limits, where they come from, today's use, the warnings and the callers", async () => {
    serve();
    render(<LimitsPage />);
    expect(screen.getByRole("status")).toHaveTextContent("読み込み中");
    const calls = (await screen.findAllByRole("row", { name: /利用者ごとの 1 日の呼び出し回数/ }))[0]; // the table of limits comes first
    expect(calls).toHaveTextContent("全体の設定");
    expect(calls).toHaveTextContent("100 / 100（100%）");
    expect(screen.getByRole("row", { name: /利用者ごとの 1 日のトークン/ })).toHaveTextContent("上限なし");
    expect(screen.getByRole("row", { name: /テナントごとの 1 日の費用の概算/ })).toHaveTextContent("テナントの設定");
    expect(screen.getByRole("row", { name: /全体の 1 日の費用の概算/ })).toHaveTextContent("上限なし");
    expect(screen.getAllByRole("meter").length).toBe(3);
    expect(screen.getByText("上限の 80% から")).toBeInTheDocument();
    expect(screen.getByText(/採点の実行は上限の対象外です/)).toBeInTheDocument();
    const reached = screen.getByRole("row", { name: /テナント tenant-a/ });
    expect(reached).toHaveTextContent("上限に達しました");
    expect(reached).toHaveTextContent("104%");
    expect(screen.getByText("上限に近づいています")).toBeInTheDocument();
    expect(screen.getAllByRole("row", { name: /abcdef123456/ }).at(-1)).toHaveTextContent("tenant-a"); // the callers table is last
    expect(screen.getByText(/1 つのキーを複数の人が使っているとき/)).toBeInTheDocument();
  });

  it("shows guidance when nothing was used today", async () => {
    serve(EMPTY_LIMITS);
    render(<LimitsPage />);
    expect(await screen.findByText(/今日はまだ、テナントの使用がありません/)).toBeInTheDocument();
    expect(screen.getByText(/今日はまだ、使用した呼び出し元がありません/)).toBeInTheDocument();
    expect(screen.getByText(/警告はありません/)).toBeInTheDocument();
  });

  it("says why there is nothing to show and tries again", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(jsonResponse({ detail: "boom" }, 500)).mockResolvedValue(jsonResponse(LIMITS));
    vi.stubGlobal("fetch", fetchMock);
    render(<LimitsPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("上限を取得できませんでした");
    fireEvent.click(screen.getByRole("button", { name: "再読み込み" }));
    expect(await screen.findByRole("row", { name: /利用者ごとの 1 日のトークン/ })).toBeInTheDocument();
  });

  it("asks for the data of one tenant", async () => {
    const fetchMock = serve();
    render(<LimitsPage />);
    await screen.findByRole("row", { name: /利用者ごとの 1 日のトークン/ });
    fireEvent.change(screen.getByLabelText(/表示するテナント ID/), { target: { value: "tenant-a" } });
    fireEvent.click(screen.getByRole("button", { name: "表示する" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url === "/admin/limits?tenant_id=tenant-a")).toBe(true));
  });

  it("changes a limit for a tenant with a reason, the CSRF token and the number", async () => {
    const fetchMock = serve(LIMITS, () => jsonResponse({ change: {}, ...LIMITS }));
    render(<LimitsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "テナントごとの 1 日のトークンを変更" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("適用範囲"), { target: { value: "tenant" } });
    fireEvent.change(within(dialog).getByLabelText("テナント ID"), { target: { value: " tenant-a " } });
    fireEvent.change(within(dialog).getByLabelText("値"), { target: { value: "60000" } });
    fireEvent.change(within(dialog).getByLabelText("理由（必須）"), { target: { value: "  増やす  " } });
    fireEvent.click(within(dialog).getByRole("button", { name: "保存" }));
    expect(await screen.findByText("上限を変更しました")).toBeInTheDocument();
    const [url, init] = lastPost(fetchMock);
    expect(url).toBe("/admin/limits");
    expect(JSON.parse(String(init.body))).toEqual({ name: "tenant_daily_tokens", value: 60000, reason: "増やす", tenant_id: "tenant-a" });
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("token-123");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("removes a setting when the value is empty, and has no tenant field for the global cost", async () => {
    const fetchMock = serve(LIMITS, () => jsonResponse({ change: {}, ...LIMITS }));
    render(<LimitsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "全体の 1 日の費用の概算を変更" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByLabelText("テナント ID")).toBeDisabled();
    expect(within(dialog).getByLabelText("適用範囲")).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText("理由（必須）"), { target: { value: "戻す" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "保存" }));
    await screen.findByText("上限を変更しました");
    expect(JSON.parse(String(lastPost(fetchMock)[1].body))).toEqual({ name: "global_daily_cost", value: null, reason: "戻す" });
  });

  it("checks the number, the tenant and the reason before sending anything", async () => {
    const fetchMock = serve();
    render(<LimitsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "利用者ごとの 1 日のトークンを変更" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("値"), { target: { value: "-5" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "保存" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("0 以上の数");
    fireEvent.change(within(dialog).getByLabelText("値"), { target: { value: "10" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "保存" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("理由を入力してください");
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("shows the text of the server when it refuses the value (400)", async () => {
    serve(LIMITS, () => jsonResponse({ detail: "a limit is a number, 0 or more" }, 400));
    render(<LimitsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "利用者ごとの 1 日のトークンを変更" }));
    fireEvent.change(screen.getByLabelText("値"), { target: { value: "10" } });
    fireEvent.change(screen.getByLabelText("理由（必須）"), { target: { value: "理由" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("a limit is a number, 0 or more");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("explains calmly that the installation-wide limits are not for this administrator (403) and keeps the page", async () => {
    serve(LIMITS, () => jsonResponse({ detail: "no" }, 403));
    render(<LimitsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "全体の 1 日の費用の概算を変更" }));
    fireEvent.change(screen.getByLabelText("値"), { target: { value: "10" } });
    fireEvent.change(screen.getByLabelText("理由（必須）"), { target: { value: "理由" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByRole("note")).toHaveTextContent("システム全体の管理者だけが変更できます");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("row", { name: /利用者ごとの 1 日のトークン/ })).toBeInTheDocument();
  });

  it("tells the administrator when someone else changed the settings first (409)", async () => {
    serve(LIMITS, () => jsonResponse({ detail: "changed" }, 409));
    render(<LimitsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "利用者ごとの 1 日のトークンを変更" }));
    fireEvent.change(screen.getByLabelText("値"), { target: { value: "10" } });
    fireEvent.change(screen.getByLabelText("理由（必須）"), { target: { value: "理由" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("他の人が先に設定を変更しました");
  });
});
