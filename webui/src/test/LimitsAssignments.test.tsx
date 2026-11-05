import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LimitsPage } from "../pages/Limits";
import { ASSIGNMENTS, LIMITS, NO_TIERS, jsonResponse } from "./limitsFixtures";

afterEach(cleanup);

beforeEach(() => {
  document.cookie = "admin_csrf=token-123";
  vi.stubGlobal("location", { ...window.location, assign: vi.fn(), pathname: "/console/limits" });
});

function serve(assignments: unknown = ASSIGNMENTS, post?: () => Response) {
  const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    if (init?.method === "POST") return Promise.resolve(post ? post() : jsonResponse({ change: {}, ...ASSIGNMENTS }));
    return Promise.resolve(jsonResponse(url.startsWith("/admin/limits") ? LIMITS : assignments));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function openTab() {
  render(<LimitsPage />);
  fireEvent.click(await screen.findByRole("tab", { name: "モデルの割り当て" }));
}

const posted = (fetchMock: ReturnType<typeof vi.fn>) => fetchMock.mock.calls.find(([, init]) => init?.method === "POST") as [string, RequestInit];

describe("the model assignments tab", () => {
  it("explains tiers and precedence, lists the profiles and the assignments, and flags a tier that is gone", async () => {
    serve();
    await openTab();
    expect(await screen.findByText(/より具体的なものが優先されます/)).toBeInTheDocument();
    expect(screen.getByText("cloud")).toBeInTheDocument();
    const gone = screen.getByRole("row", { name: /役割 operator/ });
    expect(gone).toHaveTextContent("ファイルに定義がありません");
    expect(gone).toHaveTextContent("佐藤次郎");
    expect(screen.getByRole("row", { name: /全体 全員/ })).toHaveTextContent("standard");
  });

  it("shows guidance instead of the forms when no tiers are defined", async () => {
    serve(NO_TIERS);
    await openTab();
    expect(await screen.findByText("ティアがまだ定義されていません")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "割り当てる" })).toBeNull();
  });

  it("says why there is nothing to show and tries again", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) =>
      Promise.resolve(url.startsWith("/admin/limits") ? jsonResponse(LIMITS) : jsonResponse({ detail: "x" }, 500)),
    );
    vi.stubGlobal("fetch", fetchMock);
    await openTab();
    expect(await screen.findByRole("alert")).toHaveTextContent("モデルの割り当てを取得できませんでした");
    expect(screen.getByRole("button", { name: "再読み込み" })).toBeInTheDocument();
  });

  it("assigns a tier to a caller with a reason and the CSRF token", async () => {
    const fetchMock = serve();
    await openTab();
    await screen.findByText("現在の割り当て");
    fireEvent.change(screen.getByLabelText("対象の種類"), { target: { value: "user" } });
    fireEvent.change(screen.getByLabelText(/利用者の指紋/), { target: { value: "abcdef123456" } });
    fireEvent.change(screen.getByLabelText("ティア"), { target: { value: "premium" } });
    fireEvent.change(screen.getByLabelText("理由（必須）"), { target: { value: " 長文の利用 " } });
    fireEvent.click(screen.getByRole("button", { name: "割り当てる" }));
    expect(await screen.findByText("割り当てを保存しました")).toBeInTheDocument();
    const [url, init] = posted(fetchMock);
    expect(url).toBe("/admin/llm-assignments");
    expect(JSON.parse(String(init.body))).toEqual({ kind: "user", id: "abcdef123456", tier: "premium", reason: "長文の利用" });
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("token-123");
  });

  it("assigns a tier to everyone without an id", async () => {
    const fetchMock = serve();
    await openTab();
    await screen.findByText("現在の割り当て");
    fireEvent.change(screen.getByLabelText("ティア"), { target: { value: "standard" } });
    fireEvent.change(screen.getByLabelText("理由（必須）"), { target: { value: "既定" } });
    fireEvent.click(screen.getByRole("button", { name: "割り当てる" }));
    await screen.findByText("割り当てを保存しました");
    expect(JSON.parse(String(posted(fetchMock)[1].body))).toEqual({ kind: "global", tier: "standard", reason: "既定" });
  });

  it("checks the fingerprint locally", async () => {
    const fetchMock = serve();
    await openTab();
    await screen.findByText("現在の割り当て");
    fireEvent.change(screen.getByLabelText("対象の種類"), { target: { value: "user" } });
    fireEvent.change(screen.getByLabelText(/利用者の指紋/), { target: { value: "ABC" } });
    fireEvent.change(screen.getByLabelText("ティア"), { target: { value: "premium" } });
    fireEvent.change(screen.getByLabelText("理由（必須）"), { target: { value: "理由" } });
    fireEvent.click(screen.getByRole("button", { name: "割り当てる" }));
    expect(screen.getByRole("alert")).toHaveTextContent("12 文字");
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("shows the server's text for a refused assignment (400)", async () => {
    serve(ASSIGNMENTS, () => jsonResponse({ detail: "the tier is not defined in the model profiles file" }, 400));
    await openTab();
    await screen.findByText("現在の割り当て");
    fireEvent.change(screen.getByLabelText("ティア"), { target: { value: "standard" } });
    fireEvent.change(screen.getByLabelText("理由（必須）"), { target: { value: "理由" } });
    fireEvent.click(screen.getByRole("button", { name: "割り当てる" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("the tier is not defined");
  });

  it("removes an assignment only after a confirmation with a reason", async () => {
    const fetchMock = serve();
    await openTab();
    fireEvent.click(await screen.findByRole("button", { name: "役割 operator の割り当てを取り消す" }));
    const dialog = screen.getByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "取り消す" });
    expect(confirm).toBeDisabled();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "不要" } });
    fireEvent.click(confirm);
    expect(await screen.findByText("割り当てを取り消しました")).toBeInTheDocument();
    const [, init] = posted(fetchMock);
    expect(JSON.parse(String(init.body))).toEqual({ kind: "role", id: "operator", tier: null, reason: "不要" });
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("token-123");
  });

  it("becomes read-only with a note when the administrator may not change assignments (403)", async () => {
    serve(ASSIGNMENTS, () => jsonResponse({ detail: "no" }, 403));
    await openTab();
    await screen.findByText("現在の割り当て");
    fireEvent.change(screen.getByLabelText("ティア"), { target: { value: "standard" } });
    fireEvent.change(screen.getByLabelText("理由（必須）"), { target: { value: "理由" } });
    fireEvent.click(screen.getByRole("button", { name: "割り当てる" }));
    expect(await screen.findByRole("note")).toHaveTextContent("閲覧のみ");
    await waitFor(() => expect(screen.getByRole("button", { name: "割り当てる" })).toBeDisabled());
    expect(screen.getByRole("button", { name: "役割 operator の割り当てを取り消す" })).toBeDisabled();
    expect(screen.getByText("現在の割り当て")).toBeInTheDocument();
  });
});
