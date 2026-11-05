import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AccessPage } from "../pages/Access";
import { ACCESS, routeFetch } from "./overviewFixtures";

afterEach(cleanup);

beforeEach(() => {
  vi.stubGlobal("location", { ...window.location, assign: vi.fn(), pathname: "/console/access" });
});

describe("the access page", () => {
  it("shows who you are, the roles, the authentication settings and the note", async () => {
    vi.stubGlobal("fetch", routeFetch({ "/admin/access": ACCESS }));
    render(<AccessPage />);
    expect(await screen.findByText("次のテナントのみ: sample")).toBeInTheDocument();
    expect(screen.getByRole("row", { name: /admin 管理者/ })).toBeInTheDocument();
    expect(screen.getByText("1 件（割り当て先: sample）")).toBeInTheDocument();
    expect(screen.getByText("0 件（割り当て先: なし）")).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("ID 基盤で管理されます");
    expect(screen.queryByRole("button", { name: /変更|保存/ })).toBeNull();
  });

  it("shows an error and retries", async () => {
    vi.stubGlobal("fetch", routeFetch({ "/admin/access": 500 }));
    render(<AccessPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("権限の情報を取得できませんでした");
    vi.stubGlobal("fetch", routeFetch({ "/admin/access": ACCESS }));
    fireEvent.click(screen.getByRole("button", { name: "再読み込み" }));
    expect(await screen.findByText("次のテナントのみ: sample")).toBeInTheDocument();
  });

  describe("revoking the sessions of one person", () => {
    const INSTALLATION = { ...ACCESS, you: { ...ACCESS.you, reach: "installation", tenants: null } };

    function routes(extra: Record<string, unknown> = {}) {
      const calls: Array<{ url: string; body: string }> = [];
      vi.stubGlobal(
        "fetch",
        vi.fn((url: string, init?: RequestInit) => {
          const key = `${(init?.method ?? "GET").toUpperCase()} ${String(url).split("?")[0]}`;
          if (key === "GET /admin/access") return Promise.resolve(new Response(JSON.stringify(INSTALLATION), { status: 200, headers: { "Content-Type": "application/json" } }));
          if (key === "POST /admin/sessions/revoke") {
            calls.push({ url: String(url), body: String(init?.body) });
            const status = (extra.status as number) ?? 200;
            return Promise.resolve(new Response(JSON.stringify(extra.body ?? { ok: true }), { status, headers: { "Content-Type": "application/json" } }));
          }
          return Promise.reject(new Error(`no route for ${key}`));
        }),
      );
      return calls;
    }

    it("is offered only to the installation administrator", async () => {
      vi.stubGlobal("fetch", routeFetch({ "/admin/access": ACCESS })); // reach: tenants only
      render(<AccessPage />);
      await screen.findByText("次のテナントのみ: sample");
      expect(screen.queryByRole("button", { name: "セッションを失効させる" })).toBeNull();
    });

    it("asks for a valid identifier, then for a confirmation, then revokes", async () => {
      const calls = routes();
      render(<AccessPage />);
      const input = await screen.findByLabelText("識別子（呼び出し元キー）");
      fireEvent.change(input, { target: { value: "ab" } });
      fireEvent.click(screen.getByRole("button", { name: "セッションを失効させる" }));
      expect(await screen.findByText(/6〜64 文字/)).toBeInTheDocument();
      expect(calls).toHaveLength(0);
      fireEvent.change(input, { target: { value: "key-abc123" } });
      fireEvent.click(screen.getByRole("button", { name: "セッションを失効させる" }));
      expect(await screen.findByText("識別子 key-abc123 の人のセッションをすべて失効させます。よろしいですか？")).toBeInTheDocument();
      expect(calls).toHaveLength(0); // nothing is sent before the confirmation
      const dialog = screen.getByRole("dialog");
      fireEvent.click(within(dialog).getByRole("button", { name: "セッションを失効させる" }));
      expect(await screen.findByText("識別子 key-abc123 のセッションを失効させました。")).toBeInTheDocument();
      expect(JSON.parse(calls[0].body)).toEqual({ key_fingerprint: "key-abc123" });
    });

    it("says so when the server refuses", async () => {
      routes({ status: 403, body: { detail: "this action needs the installation administrator" } });
      render(<AccessPage />);
      fireEvent.change(await screen.findByLabelText("識別子（呼び出し元キー）"), { target: { value: "key-abc123" } });
      fireEvent.click(screen.getByRole("button", { name: "セッションを失効させる" }));
      fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "セッションを失効させる" }));
      expect(await screen.findByText("この操作は、インストール全体の管理者だけが行えます。")).toBeInTheDocument();
    });
  });
});
