import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SynonymsPage } from "../pages/Synonyms";
import { jsonResponse } from "./fixtures";
import { CANDIDATES } from "./synonymsFixtures";

afterEach(cleanup);

beforeEach(() => {
  document.cookie = "admin_csrf=token-123";
  vi.stubGlobal("location", { ...window.location, assign: vi.fn(), pathname: "/console/synonyms" });
});

function stub(handler: (url: string, init?: RequestInit) => Response) {
  const mock = vi.fn((url: string, init?: RequestInit) => Promise.resolve(handler(url, init)));
  vi.stubGlobal("fetch", mock);
  return mock;
}

describe("the synonym review page", () => {
  it("lists candidates with status words and counts, loading the undecided ones first", async () => {
    const mock = stub(() => jsonResponse(CANDIDATES));
    render(<SynonymsPage />);
    expect(screen.getByRole("status")).toHaveTextContent("読み込み中");
    const row = await screen.findByRole("row", { name: /経費精算/ });
    expect(within(row).getByText("立替精算、旅費精算")).toBeInTheDocument();
    expect(within(row).getByText("未判定")).toBeInTheDocument();
    expect(screen.getByText("件数: 2")).toBeInTheDocument();
    expect(mock.mock.calls[0][0]).toBe("/admin/synonyms/candidates?status=candidate");
  });

  it("loads all statuses when asked and sends the optional token header", async () => {
    const mock = stub(() => jsonResponse(CANDIDATES));
    render(<SynonymsPage />);
    await screen.findByRole("row", { name: /経費精算/ });
    fireEvent.change(screen.getByLabelText("状態"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText(/管理トークン/), { target: { value: "tok" } });
    fireEvent.click(screen.getByRole("button", { name: "読み込み" }));
    await waitFor(() => expect(mock).toHaveBeenCalledTimes(2));
    const [url, init] = mock.mock.calls[1] as [string, RequestInit];
    expect(url).toBe("/admin/synonyms/candidates");
    expect(new Headers(init.headers).get("X-Admin-Token")).toBe("tok");
  });

  it("shows an error with retry, and an empty state", async () => {
    let n = 0;
    stub(() => (++n === 1 ? jsonResponse({ detail: "x" }, 500) : jsonResponse({ items: [], counts: {}, total: 0 })));
    render(<SynonymsPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("取得できませんでした");
    fireEvent.click(screen.getByRole("button", { name: "再読み込み" }));
    expect(await screen.findByText(/この状態の候補はありません/)).toBeInTheDocument();
  });

  it("sends a decision with the CSRF token and reloads", async () => {
    const mock = stub((url) => (url === "/admin/synonyms/decision" ? jsonResponse({ candidate_id: "c1", status: "approved" }) : jsonResponse(CANDIDATES)));
    render(<SynonymsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "承認: 経費精算" }));
    expect(await screen.findByText("更新しました")).toBeInTheDocument();
    const [url, init] = mock.mock.calls[1] as [string, RequestInit];
    expect(url).toBe("/admin/synonyms/decision");
    expect(JSON.parse(init.body as string)).toEqual({ candidate_id: "c1", status: "approved" });
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("token-123");
    await waitFor(() => expect(mock).toHaveBeenCalledTimes(3));
  });

  it("applies approved candidates only after confirmation and shows the counts", async () => {
    const mock = stub((url) =>
      url === "/admin/synonyms/apply"
        ? jsonResponse({ profile_id: "default", pinned_version: null, approved_rows: 1, entries_before: 3, entries_after: 4 })
        : jsonResponse(CANDIDATES),
    );
    render(<SynonymsPage />);
    await screen.findByRole("row", { name: /経費精算/ });
    fireEvent.click(screen.getByRole("button", { name: "承認済みをプロファイルへ反映" }));
    expect(mock).toHaveBeenCalledTimes(1); // nothing is sent before the confirmation
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "反映する" }));
    expect(await screen.findByText("反映しました: 3 → 4 件")).toBeInTheDocument();
    const [, init] = mock.mock.calls[1] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({ profile_id: "default" });
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("token-123");
  });

  it("shows a calm note and disables the actions when the list needs the installation administrator (403)", async () => {
    stub(() => jsonResponse({ detail: "forbidden" }, 403));
    render(<SynonymsPage />);
    expect(await screen.findByRole("note")).toHaveTextContent("インストール全体の管理者のみ");
    expect(screen.getByRole("button", { name: "承認済みをプロファイルへ反映" })).toBeDisabled();
  });

  it("disables the actions when a decision is refused (403) and reports an invalid profile id (400)", async () => {
    stub((url) => {
      if (url === "/admin/synonyms/decision") return jsonResponse({ detail: "forbidden" }, 403);
      if (url === "/admin/synonyms/apply") return jsonResponse({ detail: "invalid profile id" }, 400);
      return jsonResponse(CANDIDATES);
    });
    render(<SynonymsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "承認: 経費精算" }));
    expect(await screen.findByRole("note")).toHaveTextContent("インストール全体の管理者のみ");
    expect(screen.getByRole("button", { name: "却下: 経費精算" })).toBeDisabled();
  });

  it("reports an invalid profile id", async () => {
    stub((url) => (url === "/admin/synonyms/apply" ? jsonResponse({ detail: "invalid profile id" }, 400) : jsonResponse(CANDIDATES)));
    render(<SynonymsPage />);
    await screen.findByRole("row", { name: /経費精算/ });
    fireEvent.click(screen.getByRole("button", { name: "承認済みをプロファイルへ反映" }));
    fireEvent.click(screen.getByRole("button", { name: "反映する" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("プロファイルIDが正しくありません");
  });
});
