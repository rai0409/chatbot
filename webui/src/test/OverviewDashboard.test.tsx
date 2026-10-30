import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DashboardPage } from "../pages/Dashboard";
import { DAILY, HEALTH, JOBS, METRICS, RECORD_STATS, STATUS, routeFetch } from "./overviewFixtures";

afterEach(cleanup);

const OK_ROUTES = {
  "/admin/metrics/summary": METRICS,
  "/admin/health": HEALTH,
  "/admin/ingestion/jobs": JOBS,
  "/admin/system/status": STATUS,
  "/admin/records/stats?days=7": RECORD_STATS,
  "/admin/usage/daily?days=7": DAILY,
};

const show = () =>
  render(
    <MemoryRouter>
      <DashboardPage />
    </MemoryRouter>,
  );

beforeEach(() => {
  document.cookie = "admin_csrf=token-123";
  vi.stubGlobal("location", { ...window.location, assign: vi.fn(), pathname: "/console/" });
});

describe("the dashboard", () => {
  it("shows the KPIs, alerts, modes, dependencies, jobs, records and LLM use", async () => {
    vi.stubGlobal("fetch", routeFetch(OK_ROUTES));
    show();
    expect(await screen.findByText("回答数")).toBeInTheDocument();
    expect(screen.getByText("80%")).toBeInTheDocument(); // (6+2)/10
    expect(screen.getByText("20%")).toBeInTheDocument(); // fallback 2/10
    expect(screen.getByText("3 / 1")).toBeInTheDocument();
    expect(screen.getByRole("row", { name: /回答不能率/ })).toHaveTextContent("注意");
    expect(screen.getByRole("row", { name: /文書から回答/ })).toHaveTextContent("6");
    expect(await screen.findByRole("row", { name: /qdrant/ })).toHaveTextContent("接続不可");
    expect(screen.getByRole("row", { name: /redis/ })).toHaveTextContent("3 ms");
    expect(screen.getByText(/embed-model/)).toBeInTheDocument();
    expect(await screen.findByRole("row", { name: /abcdef12/ })).toHaveTextContent("docs");
    expect(screen.getByRole("link", { name: "すべて表示" })).toHaveAttribute("href", "/ingestion");
    expect(await screen.findByText("悪い評価で未レビュー")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "記録とレビューを開く" })).toHaveAttribute("href", "/records");
    expect(await screen.findByText("0.3700")).toBeInTheDocument(); // cost of the two days
  });

  it("shows a banner with a link when the LLM is failing", async () => {
    vi.stubGlobal("fetch", routeFetch(OK_ROUTES));
    show();
    const note = await screen.findByRole("note");
    expect(note).toHaveTextContent("LLM が障害");
    expect(note).toHaveTextContent("オフになっています");
    expect(within(note).getByRole("link")).toHaveAttribute("href", "/system");
  });

  it("keeps the other blocks when one fails, and the failed block can be retried", async () => {
    const fetchMock = routeFetch({ ...OK_ROUTES, "/admin/health": 500 });
    vi.stubGlobal("fetch", fetchMock);
    show();
    expect(await screen.findByRole("alert")).toHaveTextContent("依存サービスの状態を取得できませんでした");
    expect(screen.getByText("回答数")).toBeInTheDocument();
    expect(await screen.findByRole("row", { name: /abcdef12/ })).toBeInTheDocument();
    vi.stubGlobal("fetch", routeFetch(OK_ROUTES));
    fireEvent.click(screen.getByRole("button", { name: "再読み込み" }));
    expect(await screen.findByRole("row", { name: /redis/ })).toBeInTheDocument();
  });

  it("says what to do when there is nothing yet", async () => {
    vi.stubGlobal("fetch", routeFetch({ ...OK_ROUTES, "/admin/ingestion/jobs": { jobs: [] }, "/admin/usage/daily?days=7": { days: 7, rows: [] } }));
    show();
    expect(await screen.findByText("ジョブはありません。")).toBeInTheDocument();
    expect(await screen.findByText("この期間の LLM 呼び出しはありません。")).toBeInTheDocument();
  });

  it("ends all sessions only after a confirmation, with the CSRF token", async () => {
    const fetchMock = routeFetch({ ...OK_ROUTES, "/admin/logout-all": { ok: true } });
    vi.stubGlobal("fetch", fetchMock);
    show();
    fireEvent.click(await screen.findByRole("button", { name: "すべての管理セッションを終了" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("よろしいですか");
    expect(fetchMock.mock.calls.some(([u]) => String(u) === "/admin/logout-all")).toBe(false);
    fireEvent.click(within(dialog).getByRole("button", { name: "終了する" }));
    await waitFor(() => expect(window.location.assign).toHaveBeenCalledWith("/admin/login"));
    const call = fetchMock.mock.calls.find(([u]) => String(u) === "/admin/logout-all")!;
    expect((call[1] as RequestInit).method).toBe("POST");
    expect(new Headers((call[1] as RequestInit).headers).get("X-CSRF-Token")).toBe("token-123");
  });

  it("explains a refusal (403) of the session end", async () => {
    vi.stubGlobal("fetch", routeFetch({ ...OK_ROUTES, "/admin/logout-all": 403 }));
    show();
    fireEvent.click(await screen.findByRole("button", { name: "すべての管理セッションを終了" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "終了する" }));
    expect(await screen.findByText("この操作は、この管理者には許可されていません。")).toBeInTheDocument();
    expect(window.location.assign).not.toHaveBeenCalled();
  });
});
