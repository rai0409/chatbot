import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { UsagePage } from "../pages/Usage";
import { DAILY, DOWNTIME, USAGE, routeFetch } from "./overviewFixtures";

afterEach(cleanup);

beforeEach(() => {
  vi.stubGlobal("location", { ...window.location, assign: vi.fn(), pathname: "/console/usage" });
});

const AVAILABILITY = {
  configured: true,
  log_file_found: true,
  probes: 100,
  availability_percent: 99.5,
  meets_target: true,
  target_percent: 99,
  coverage_percent: 98.2,
  business_minutes: { total: 600, up: 597, down: 3, unmonitored: 10.8, degraded_but_up: 20 },
  outages: [{ start: "2026-10-28T02:00:00+00:00", end: "2026-10-28T02:03:00+00:00", business_minutes: 3, error: "HTTP 503" }],
  unmonitored_gaps: [],
};
const ROUTES = {
  "/admin/usage?": USAGE,
  "/admin/usage/daily": DAILY,
  "/admin/usage/downtime": DOWNTIME,
  "/admin/availability": AVAILABILITY,
};

describe("the usage page", () => {
  it("shows the totals, the daily table and the downtime with the business hours", async () => {
    const fetchMock = routeFetch(ROUTES);
    vi.stubGlobal("fetch", fetchMock);
    render(<UsagePage />);
    const row = await screen.findByRole("row", { name: /sample/ });
    expect(row).toHaveTextContent("150"); // tokens
    expect(row).toHaveTextContent("0.0123");
    expect(row).toHaveTextContent("500 ミリ秒"); // 2000 ms / 4 calls
    expect(fetchMock.mock.calls[0][0]).toBe("/admin/usage?days=7&by=tenant_id");
    expect(screen.getByText(/費用は単価からの概算/)).toBeInTheDocument();
    expect(await screen.findByRole("row", { name: /2026-10-28/ })).toHaveTextContent("20");
    expect(await screen.findByText(/平日 9時〜18時（UTC\+9）/)).toBeInTheDocument();
    expect(screen.getByText(/抽出型で続けられます/)).toBeInTheDocument();
    const breaker = screen.getByRole("row", { name: /local:profile/ });
    expect(breaker).toHaveTextContent("1時間2分"); // 3725 s
    expect(breaker).toHaveTextContent("30分");
  });

  it("asks again when the period or the grouping changes", async () => {
    const fetchMock = routeFetch(ROUTES);
    vi.stubGlobal("fetch", fetchMock);
    render(<UsagePage />);
    await screen.findByRole("row", { name: /sample/ });
    fireEvent.change(screen.getByLabelText("期間"), { target: { value: "30" } });
    fireEvent.change(screen.getByLabelText("集計の単位"), { target: { value: "model" } });
    await screen.findByRole("row", { name: /（なし）/ }); // the fixture has no model column: shown as blank
    const urls = fetchMock.mock.calls.map(([u]) => String(u));
    expect(urls).toContain("/admin/usage?days=30&by=model");
    expect(urls).toContain("/admin/usage/downtime?days=30");
  });

  it("shows the availability from the external probe, the outages, and what is missing", async () => {
    vi.stubGlobal("fetch", routeFetch(ROUTES));
    const { unmount } = render(<UsagePage />);
    expect(await screen.findByText("99.5%")).toBeInTheDocument();
    expect(screen.getByText(/目標 99%: 達成/)).toBeInTheDocument();
    expect(screen.getByText(/監視できなかった時間 10.8 分/)).toBeInTheDocument();
    expect(screen.getByText(/2026-10-28T02:00:00\+00:00 〜 2026-10-28T02:03:00\+00:00（営業時間内 3 分）/)).toBeInTheDocument();
    unmount();
    cleanup();
    vi.stubGlobal("fetch", routeFetch({ ...ROUTES, "/admin/availability": { configured: false } }));
    render(<UsagePage />);
    expect(await screen.findByText(/外部監視の記録が設定されていません/)).toBeInTheDocument();
  });

  it("says that only the installation administrator sees the availability (403), and when the log is missing", async () => {
    vi.stubGlobal("fetch", routeFetch({ ...ROUTES, "/admin/availability": 403 }));
    const { unmount } = render(<UsagePage />);
    expect(await screen.findByText("稼働率は、インストール全体の管理者だけが見られます。")).toBeInTheDocument();
    unmount();
    cleanup();
    vi.stubGlobal("fetch", routeFetch({ ...ROUTES, "/admin/availability": { configured: true, log_file_found: false } }));
    render(<UsagePage />);
    expect(await screen.findByText("設定された記録のファイルが見つかりません。")).toBeInTheDocument();
  });

  it("says that only the installation administrator sees the downtime (403)", async () => {
    vi.stubGlobal("fetch", routeFetch({ ...ROUTES, "/admin/usage/downtime": 403 }));
    render(<UsagePage />);
    expect(await screen.findByText("停止時間は、インストール全体の管理者だけが見られます。")).toBeInTheDocument();
    expect(await screen.findByRole("row", { name: /sample/ })).toBeInTheDocument();
  });

  it("shows an error with a retry, and an empty state", async () => {
    vi.stubGlobal("fetch", routeFetch({ ...ROUTES, "/admin/usage?": 500, "/admin/usage/daily": { days: 7, rows: [] } }));
    render(<UsagePage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("使用量を取得できませんでした");
    expect(await screen.findByText("この期間の日ごとのデータはありません。")).toBeInTheDocument();
    vi.stubGlobal("fetch", routeFetch({ ...ROUTES, "/admin/usage?": { days: 7, by: "tenant_id", rows: [] } }));
    fireEvent.click(within(screen.getByRole("alert")).getByRole("button", { name: "再読み込み" }));
    expect(await screen.findByText(/この期間の呼び出しはありません/)).toBeInTheDocument();
  });
});
