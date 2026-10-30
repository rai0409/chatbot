import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
});
