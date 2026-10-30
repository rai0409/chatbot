import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ContractsPage } from "../pages/Contracts";
import { NOTIFY_DRY, contractsFetch } from "./contractsFixtures";
import { jsonResponse } from "./fixtures";

afterEach(cleanup);

beforeEach(() => {
  document.cookie = "admin_csrf=token-123";
  vi.stubGlobal("location", { ...window.location, assign: vi.fn(), pathname: "/console/contracts" });
});

function setup(fetchImpl = contractsFetch()) {
  const fetchMock = vi.fn(fetchImpl);
  vi.stubGlobal("fetch", fetchMock);
  render(
    <MemoryRouter>
      <ContractsPage />
    </MemoryRouter>,
  );
  return fetchMock;
}

const posts = (m: ReturnType<typeof vi.fn>) => m.mock.calls.filter((c) => (c[1]?.method ?? "GET") === "POST");

async function openDetail() {
  fireEvent.click(await screen.findByRole("button", { name: /保守契約 \/ 株式会社サンプル の詳細を開く/ }));
  return screen.findByRole("region", { name: "service-2026-01" });
}

describe("the contract list", () => {
  it("shows contracts with status, nearest deadline marked unconfirmed, and upcoming deadlines", async () => {
    setup();
    const row = (await screen.findAllByRole("row", { name: /保守契約/ }))[0];
    expect(within(row).getByText("要確認")).toBeInTheDocument();
    expect(within(row).getByText(/2026-12-31 契約の終了日（未確認）/)).toBeInTheDocument();
    expect(screen.getByText("契約数: 2")).toBeInTheDocument();
    expect(await screen.findByText("28 日超過")).toBeInTheDocument();
    expect(screen.getByText("残り 63 日")).toBeInTheDocument();
  });

  it("shows an error with a retry, and an empty state that says what to do", async () => {
    let fail = true;
    setup(
      contractsFetch([
        (url) => (url.startsWith("/admin/contracts/list") ? (fail ? jsonResponse({ detail: "x" }, 500) : jsonResponse({ contracts: [] })) : undefined),
      ]),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("契約の一覧を読み込めませんでした");
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "再読み込み" }));
    expect(await screen.findByText(/抽出結果を取り込む」から契約を追加/)).toBeInTheDocument();
  });

  it("loads another tenant with the tenant in the query", async () => {
    const m = setup();
    await screen.findAllByRole("row", { name: /保守契約/ });
    fireEvent.change(screen.getByLabelText("テナント"), { target: { value: "t 2" } });
    fireEvent.click(screen.getByRole("button", { name: "一覧を読み込む" }));
    await waitFor(() => expect(m.mock.calls.some((c) => c[0] === "/admin/contracts/list?tenant_id=t%202")).toBe(true));
  });
});

describe("the contract detail", () => {
  it("keeps extracted values visibly unconfirmed", async () => {
    setup();
    const detail = await openDetail();
    expect(within(detail).getByText(/未確認の項目が 2 件あります/)).toBeInTheDocument();
    const row = within(detail).getByRole("row", { name: /相手方/ });
    expect(within(row).getByText("未確認")).toBeInTheDocument();
    expect(within(row).getByText(/p\.1 甲: 株式会社サンプル/)).toBeInTheDocument();
    expect(within(within(detail).getByRole("row", { name: /件名/ })).getByText("確認済み")).toBeInTheDocument();
    expect(within(detail).getByRole("button", { name: "連絡先メール を確認" })).toBeDisabled(); // no value to confirm
  });

  it("confirms a field with the CSRF token", async () => {
    const m = setup();
    const detail = await openDetail();
    fireEvent.click(within(detail).getByRole("button", { name: "相手方 を確認" }));
    expect(await screen.findByText("保存しました")).toBeInTheDocument();
    const [url, init] = posts(m)[0];
    expect(url).toBe("/admin/contracts/service-2026-01/confirm");
    expect(JSON.parse(init.body)).toEqual({ tenant_id: "default", name: "counterparty" });
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("token-123");
  });

  it("saves a corrected value", async () => {
    const m = setup();
    const detail = await openDetail();
    fireEvent.change(within(detail).getByLabelText("終了日 の値"), { target: { value: "2027-03-31" } });
    fireEvent.click(within(detail).getByRole("button", { name: "終了日 を訂正して保存" }));
    await screen.findByText("保存しました");
    const [url, init] = posts(m)[0];
    expect(url).toBe("/admin/contracts/service-2026-01/field");
    expect(JSON.parse(init.body)).toEqual({ tenant_id: "default", name: "end_date", value: "2027-03-31" });
  });

  it("explains a conflict (409) and a refusal of the value (400)", async () => {
    let status = 409;
    setup(contractsFetch([(url) => (url.endsWith("/field") ? jsonResponse({ detail: status === 409 ? "the contract was changed by someone else; reload it" : "end_date: not a date" }, status) : undefined)]));
    const detail = await openDetail();
    fireEvent.click(within(detail).getByRole("button", { name: "終了日 を訂正して保存" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("他の人が先にこの契約を変更しました");
    status = 400;
    fireEvent.click(within(detail).getByRole("button", { name: "終了日 を訂正して保存" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("保存できませんでした: end_date: not a date"));
  });

  it("says plainly when the administrator may not change it (403)", async () => {
    setup(contractsFetch([(url) => (url.endsWith("/confirm") ? jsonResponse({ detail: "forbidden" }, 403) : undefined)]));
    const detail = await openDetail();
    fireEvent.click(within(detail).getByRole("button", { name: "相手方 を確認" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("この操作をする権限がありません");
  });

  it("shows an error with retry when the detail cannot be read", async () => {
    setup(contractsFetch([(url) => (url.startsWith("/admin/contracts/service-2026-01") ? jsonResponse({ detail: "x" }, 500) : undefined)]));
    fireEvent.click(await screen.findByRole("button", { name: /保守契約 \/ 株式会社サンプル の詳細を開く/ }));
    expect(await screen.findByText("契約を取得できませんでした。")).toBeInTheDocument();
  });
});

describe("the Outlook draft", () => {
  it("asks for the mailbox and creates a draft that is not sent", async () => {
    const m = setup(contractsFetch([(url) => (url.endsWith("/draft") ? jsonResponse({ draft_id: "d1", kind: "end", date: "2026-12-31" }) : undefined)]));
    const detail = await openDetail();
    fireEvent.click(within(detail).getByRole("button", { name: /契約の終了日: Outlook の下書きを作る/ }));
    const dialog = screen.getByRole("dialog");
    const create = within(dialog).getByRole("button", { name: "下書きを作る" });
    expect(create).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText(/メールボックス/), { target: { value: "taro@example.co.jp" } });
    fireEvent.click(create);
    expect(await screen.findByText(/下書きを作りました（送信はしていません）/)).toBeInTheDocument();
    const [url, init] = posts(m)[0];
    expect(url).toBe("/admin/contracts/service-2026-01/draft");
    expect(JSON.parse(init.body)).toEqual({ tenant_id: "default", kind: "end", mailbox: "taro@example.co.jp" });
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("token-123");
  });

  it("explains calmly that the integration is off and links to the system status", async () => {
    setup(contractsFetch([(url) => (url.endsWith("/draft") ? jsonResponse({ detail: "integration_off:switch_off" }, 409) : undefined)]));
    const detail = await openDetail();
    fireEvent.click(within(detail).getByRole("button", { name: /Outlook の下書きを作る/ }));
    fireEvent.change(screen.getByLabelText(/メールボックス/), { target: { value: "taro@example.co.jp" } });
    fireEvent.click(screen.getByRole("button", { name: "下書きを作る" }));
    expect(await screen.findByText("外部連携がオフのため、下書きは作成できません")).toBeInTheDocument();
    expect(screen.getByText(/コンソールのスイッチがオフです/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /システム状態/ })).toHaveAttribute("href", "/system");
    expect(screen.queryByText(/作れませんでした/)).toBeNull();
    // the rest keeps working
    expect(within(detail).getByRole("button", { name: "相手方 を確認" })).toBeEnabled();
  });

  it("reports another failure as a failure", async () => {
    setup(contractsFetch([(url) => (url.endsWith("/draft") ? jsonResponse({ detail: "the contact e-mail has not been confirmed by a person" }, 400) : undefined)]));
    const detail = await openDetail();
    fireEvent.click(within(detail).getByRole("button", { name: /Outlook の下書きを作る/ }));
    fireEvent.change(screen.getByLabelText(/メールボックス/), { target: { value: "taro@example.co.jp" } });
    fireEvent.click(screen.getByRole("button", { name: "下書きを作る" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("作れませんでした: the contact e-mail has not been confirmed");
  });
});

describe("the reminder notification", () => {
  it("runs a dry run without asking and sends nothing", async () => {
    const m = setup();
    await screen.findAllByRole("row", { name: /保守契約/ });
    fireEvent.click(screen.getByRole("button", { name: "通知の予定を見る（送らない）" }));
    expect(await screen.findByText("通知の予定: 1 件（送信 0 / 失敗 0）")).toBeInTheDocument();
    const [url, init] = posts(m)[0];
    expect(url).toBe("/admin/contracts/notify");
    expect(JSON.parse(init.body)).toEqual({ tenant_id: "default", dry_run: true });
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("token-123");
    expect(screen.getByRole("row", { name: /service-2026-01/ })).toHaveTextContent("予定");
  });

  it("asks before it sends, and can be cancelled", async () => {
    const m = setup(contractsFetch([(url) => (url.endsWith("/notify") ? jsonResponse({ ...NOTIFY_DRY, dry_run: false, sent: 1, results: [{ ...NOTIFY_DRY.results[0], status: "sent" }] }) : undefined)]));
    await screen.findAllByRole("row", { name: /保守契約/ });
    fireEvent.click(screen.getByRole("button", { name: "通知を送る" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "キャンセル" }));
    expect(posts(m)).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "通知を送る" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "送信する" }));
    expect(await screen.findByText("送信しました: 1 件（送信 1 / 失敗 0）")).toBeInTheDocument();
    expect(JSON.parse(posts(m)[0][1].body)).toEqual({ tenant_id: "default", dry_run: false });
  });

  it("explains calmly that the e-mail integration is off when the result says so", async () => {
    const off = { ...NOTIFY_DRY, dry_run: false, failed: 1, results: [{ ...NOTIFY_DRY.results[0], status: "failed", reason: "integration_off:tenant_off" }] };
    setup(contractsFetch([(url) => (url.endsWith("/notify") ? jsonResponse(off) : undefined)]));
    await screen.findAllByRole("row", { name: /保守契約/ });
    fireEvent.click(screen.getByRole("button", { name: "通知を送る" }));
    fireEvent.click(screen.getByRole("button", { name: "送信する" }));
    expect(await screen.findByText(/外部連携がオフのため、通知メールは送信できません/, { selector: "b" })).toBeInTheDocument();
    expect(screen.getByText(/このテナントでは無効にされています/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /システム状態/ })).toHaveAttribute("href", "/system");
  });

  it("shows a failure to run it", async () => {
    setup(contractsFetch([(url) => (url.endsWith("/notify") ? jsonResponse({ detail: "boom" }, 500) : undefined)]));
    await screen.findAllByRole("row", { name: /保守契約/ });
    fireEvent.click(screen.getByRole("button", { name: "通知の予定を見る（送らない）" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("実行できませんでした: boom");
  });
});

describe("importing an extraction", () => {
  const json = '{"schema": "contract-extract/v1", "fields": {"counterparty": {"value": "株式会社サンプル"}}}';

  it("rejects text that is not JSON without calling the server", async () => {
    const m = setup();
    await screen.findAllByRole("row", { name: /保守契約/ });
    fireEvent.change(screen.getByLabelText("契約 ID", { exact: false }), { target: { value: "service-2026-02" } });
    fireEvent.change(screen.getByLabelText(/抽出結果（JSON）/), { target: { value: "{not json" } });
    fireEvent.click(screen.getByRole("button", { name: "取り込む（未確認として保存）" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("JSON として読めません");
    expect(posts(m)).toHaveLength(0);
  });

  it("imports as unconfirmed, reports rejected fields and reloads the list", async () => {
    const m = setup(contractsFetch([(url) => (url.endsWith("/import") ? jsonResponse({ contract: {}, rejected: { end_date: "not stored: not a date" }, kept: [] }) : undefined)]));
    await screen.findAllByRole("row", { name: /保守契約/ });
    fireEvent.change(screen.getByLabelText("契約 ID", { exact: false }), { target: { value: " service-2026-02 " } });
    fireEvent.change(screen.getByLabelText("元の文書名"), { target: { value: "b.pdf" } });
    fireEvent.change(screen.getByLabelText(/抽出結果（JSON）/), { target: { value: json } });
    fireEvent.click(screen.getByRole("button", { name: "取り込む（未確認として保存）" }));
    expect(await screen.findByText("取り込みました（すべて未確認）。保存されなかった項目: 1")).toBeInTheDocument();
    expect(screen.getByText(/終了日: not stored: not a date/)).toBeInTheDocument();
    const [url, init] = posts(m)[0];
    expect(url).toBe("/admin/contracts/import");
    expect(JSON.parse(init.body)).toEqual({ tenant_id: "default", contract_id: "service-2026-02", source_doc: "b.pdf", extracted: JSON.parse(json) });
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("token-123");
    await waitFor(() => expect(m.mock.calls.filter((c) => String(c[0]).startsWith("/admin/contracts/list")).length).toBeGreaterThan(1));
  });

  it("explains a duplicate contract (409)", async () => {
    setup(contractsFetch([(url) => (url.endsWith("/import") ? jsonResponse({ detail: "the contract was changed by someone else; reload it" }, 409) : undefined)]));
    await screen.findAllByRole("row", { name: /保守契約/ });
    fireEvent.change(screen.getByLabelText("契約 ID", { exact: false }), { target: { value: "x" } });
    fireEvent.change(screen.getByLabelText(/抽出結果（JSON）/), { target: { value: json } });
    fireEvent.click(screen.getByRole("button", { name: "取り込む（未確認として保存）" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("他の人が先にこの契約を変更しました");
  });
});
