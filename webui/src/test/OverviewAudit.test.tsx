import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuditPage } from "../pages/Audit";
import { AUDIT, routeFetch } from "./overviewFixtures";

afterEach(cleanup);

beforeEach(() => {
  vi.stubGlobal("location", { ...window.location, assign: vi.fn(), pathname: "/console/audit" });
});

const urls = (m: ReturnType<typeof routeFetch>) => m.mock.calls.map(([u]) => String(u));

describe("the audit page", () => {
  it("lists the events with a summary, expands one to escaped JSON, and says how questions are protected", async () => {
    vi.stubGlobal("fetch", routeFetch({ "/admin/audit?": AUDIT }));
    render(<AuditPage />);
    const row = await screen.findByRole("row", { name: /integration_switch/ });
    expect(row).toHaveTextContent("sample");
    expect(row).toHaveTextContent("integration=slack");
    expect(screen.getByText("1〜2 件目 / 全 120 件")).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("AUDIT_QUESTION_MODE");
    fireEvent.click(within(row).getByRole("button", { name: "JSON を表示" }));
    const pre = document.querySelector("pre")!;
    expect(pre.textContent).toContain('"note": "<b>x</b>"');
    expect(pre.querySelector("b")).toBeNull(); // shown as text, not as markup
  });

  it("filters by text and action, pages by 50, and switches the kind", async () => {
    const fetchMock = routeFetch({ "/admin/audit?": AUDIT });
    vi.stubGlobal("fetch", fetchMock);
    render(<AuditPage />);
    await screen.findByRole("row", { name: /integration_switch/ });
    expect(urls(fetchMock)[0]).toBe("/admin/audit?kind=admin&limit=50&offset=0");
    fireEvent.change(screen.getByLabelText("テキストで絞り込み"), { target: { value: "slack" } });
    fireEvent.change(screen.getByLabelText("操作名で絞り込み"), { target: { value: "integration_switch" } });
    fireEvent.click(screen.getByRole("button", { name: "絞り込む" }));
    await screen.findByRole("row", { name: /integration_switch/ });
    expect(urls(fetchMock)).toContain("/admin/audit?kind=admin&limit=50&offset=0&text=slack&action=integration_switch");
    fireEvent.click(screen.getByRole("button", { name: "次の 50 件" }));
    await screen.findByRole("row", { name: /integration_switch/ });
    expect(urls(fetchMock)).toContain("/admin/audit?kind=admin&limit=50&offset=50&text=slack&action=integration_switch");
    expect(screen.getByRole("button", { name: "前の 50 件" })).toBeEnabled();
    fireEvent.click(screen.getByRole("tab", { name: "フィードバック" }));
    await screen.findByRole("row", { name: /integration_switch/ });
    expect(urls(fetchMock).at(-1)).toBe("/admin/audit?kind=feedback&limit=50&offset=0");
  });

  it("says what to do when nothing matches, and shows an error with a retry", async () => {
    vi.stubGlobal("fetch", routeFetch({ "/admin/audit?": { ...AUDIT, total: 0, events: [] } }));
    render(<AuditPage />);
    expect(await screen.findByText(/該当する記録がありません/)).toBeInTheDocument();
    cleanup();
    vi.stubGlobal("fetch", routeFetch({ "/admin/audit?": 500 }));
    render(<AuditPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("監査ログを取得できませんでした");
  });

  it("verifies the chain: ok, broken, and refused (403)", async () => {
    vi.stubGlobal("fetch", routeFetch({ "/admin/audit?": AUDIT, "/admin/audit/verify": { ok: true, checked: 12 } }));
    render(<AuditPage />);
    await screen.findByRole("row", { name: /integration_switch/ });
    fireEvent.click(screen.getByRole("button", { name: "ログのつながりを検証" }));
    expect(await screen.findByText("問題はありません（12 件を確認しました）。")).toBeInTheDocument();

    vi.stubGlobal("fetch", routeFetch({ "/admin/audit?": AUDIT, "/admin/audit/verify": { ok: false, file: "/runs/audit/events.jsonl", line: 7, checked: 6 } }));
    fireEvent.click(screen.getByRole("button", { name: "ログのつながりを検証" }));
    expect(await screen.findByText(/events\.jsonl の 7 行目/)).toBeInTheDocument();

    vi.stubGlobal("fetch", routeFetch({ "/admin/audit?": AUDIT, "/admin/audit/verify": 403 }));
    fireEvent.click(screen.getByRole("button", { name: "ログのつながりを検証" }));
    expect(await screen.findByText("検証は、インストール全体の管理者だけが行えます。")).toBeInTheDocument();
  });
});
