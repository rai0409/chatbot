import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RecordsPage } from "../pages/Records";
import { jsonResponse } from "./fixtures";
import { DRY, LEVEL, PAGE, REAL, router, sent } from "./recordsFixtures";

afterEach(cleanup);

beforeEach(() => {
  document.cookie = "admin_csrf=token-123";
  vi.stubGlobal("location", { ...window.location, assign: vi.fn(), pathname: "/console/records" });
});

async function openTab(name: string) {
  render(
    <MemoryRouter>
      <RecordsPage />
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByRole("tab", { name }));
}

type Routes = Parameters<typeof router>[0];

function stub(extra: Routes) {
  const fetchMock = vi.fn(router({ "GET /admin/records": () => jsonResponse(PAGE), ...extra }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("the record level settings", () => {
  it("explains the four levels, the default, the ceiling and the level of each tenant", async () => {
    stub({ "GET /admin/records/level": () => jsonResponse(LEVEL) });
    await openTab("記録の設定");
    expect(await screen.findByText(/何も残しません/)).toBeInTheDocument();
    expect(screen.getByText(/やり取りの形だけを残します/)).toBeInTheDocument();
    expect(screen.getByText(/質問者の仮名、質問、回答の本文、根拠を残します/)).toBeInTheDocument();
    expect(screen.getByText(/接続元のアドレスとブラウザの情報を残します/)).toBeInTheDocument();
    expect(screen.getByText("上限を超えているため選べません")).toBeInTheDocument(); // detailed is above the ceiling
    expect(screen.getByText("環境で決められた上限").nextSibling).toHaveTextContent("標準");
    expect(screen.getByRole("row", { name: /sample-tenant/ })).toHaveTextContent("標準");
  });

  it("shows an error with a retry button", async () => {
    let calls = 0;
    stub({ "GET /admin/records/level": () => (calls++ === 0 ? jsonResponse({ detail: "x" }, 500) : jsonResponse(LEVEL)) });
    await openTab("記録の設定");
    expect(await screen.findByRole("alert")).toHaveTextContent("読み込めませんでした");
    fireEvent.click(screen.getByRole("button", { name: "再読み込み" }));
    expect(await screen.findByText(/何も残しません/)).toBeInTheDocument();
  });

  it("changes the global level with a reason, never above the ceiling", async () => {
    const after = { ...LEVEL, global: "standard", effective: "standard" };
    const fetchMock = stub({
      "GET /admin/records/level": () => jsonResponse(LEVEL),
      "POST /admin/records/level": () => jsonResponse({ change: {}, ...after }),
    });
    await openTab("記録の設定");
    fireEvent.click(await screen.findByRole("button", { name: "全体の水準を変更" }));
    const dialog = within(screen.getByRole("dialog"));
    expect(dialog.getByLabelText(/詳細/)).toBeDisabled();
    const confirm = dialog.getByRole("button", { name: "実行" });
    fireEvent.click(dialog.getByLabelText("標準"));
    expect(confirm).toBeDisabled(); // no reason, no change
    fireEvent.change(dialog.getByLabelText("変更の理由（必須）"), { target: { value: " 評価のため " } });
    fireEvent.click(confirm);
    expect(await screen.findByText("水準を変更しました")).toBeInTheDocument();
    const [call] = sent(fetchMock);
    expect(call.body).toEqual({ level: "standard", reason: "評価のため" });
    expect(call.token).toBe("token-123");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("changes the level of one tenant and removes a tenant's own setting (level null)", async () => {
    const fetchMock = stub({
      "GET /admin/records/level": () => jsonResponse(LEVEL),
      "POST /admin/records/level": () => jsonResponse({ change: {}, ...LEVEL }),
    });
    await openTab("記録の設定");
    fireEvent.click(await screen.findByRole("button", { name: "sample-tenant の水準を変更" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByLabelText("匿名"));
    fireEvent.change(within(screen.getByRole("dialog")).getByLabelText("変更の理由（必須）"), { target: { value: "縮小" } });
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "実行" }));
    await screen.findByText("水準を変更しました");
    fireEvent.click(screen.getByRole("button", { name: "sample-tenant の独自設定を外す" }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "全体に合わせる" } });
    fireEvent.click(screen.getByRole("button", { name: "実行" }));
    await waitFor(() => expect(sent(fetchMock)).toHaveLength(2));
    expect(sent(fetchMock).map((call) => call.body)).toEqual([
      { level: "anonymous", reason: "縮小", tenant_id: "sample-tenant" },
      { level: null, reason: "全体に合わせる", tenant_id: "sample-tenant" },
    ]);
  });

  it("shows the server's message for a 400 and a plain sentence for a 403", async () => {
    const status = { code: 400 };
    stub({
      "GET /admin/records/level": () => jsonResponse(LEVEL),
      "POST /admin/records/level": () => jsonResponse({ detail: "the level 'detailed' is above RECORD_LEVEL_MAX (standard)" }, status.code),
    });
    await openTab("記録の設定");
    async function attempt() {
      fireEvent.click(await screen.findByRole("button", { name: "全体の水準を変更" }));
      const dialog = within(screen.getByRole("dialog"));
      fireEvent.click(dialog.getByLabelText("標準"));
      fireEvent.change(dialog.getByLabelText("変更の理由（必須）"), { target: { value: "試す" } });
      fireEvent.click(dialog.getByRole("button", { name: "実行" }));
    }
    await attempt();
    expect(await screen.findByRole("alert")).toHaveTextContent("水準を変更できませんでした: the level 'detailed' is above RECORD_LEVEL_MAX");
    status.code = 403;
    await attempt();
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("全体の水準を変えられるのは、全体の管理者だけです"));
  });

  it("asks for the tenant id when adding a tenant's setting", async () => {
    const fetchMock = stub({
      "GET /admin/records/level": () => jsonResponse(LEVEL),
      "POST /admin/records/level": () => jsonResponse({ change: {}, ...LEVEL }),
    });
    await openTab("記録の設定");
    fireEvent.click(await screen.findByRole("button", { name: "テナントの設定を追加" }));
    const dialog = within(screen.getByRole("dialog"));
    fireEvent.click(dialog.getByLabelText("匿名"));
    fireEvent.change(dialog.getByLabelText("変更の理由（必須）"), { target: { value: "追加" } });
    fireEvent.click(dialog.getByRole("button", { name: "実行" }));
    expect(dialog.getByRole("alert")).toHaveTextContent("テナント ID を入力してください");
    fireEvent.change(dialog.getByLabelText("テナント ID"), { target: { value: "tenant-x" } });
    fireEvent.click(dialog.getByRole("button", { name: "実行" }));
    await screen.findByText("水準を変更しました");
    expect(sent(fetchMock)[0].body).toEqual({ level: "anonymous", reason: "追加", tenant_id: "tenant-x" });
  });
});

describe("the retention tab", () => {
  it("explains retention and runs a dry run that changes nothing", async () => {
    const fetchMock = stub({ "POST /admin/records/purge": () => jsonResponse(DRY) });
    await openTab("保持の処理");
    expect(screen.getByText(/RECORDS_RETENTION_DAYS/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "試しに実行（何も変えません）" }));
    const result = await screen.findByRole("region", { name: /試しに実行した結果/ });
    expect(result).toHaveTextContent("匿名化される記録12");
    expect(result).toHaveTextContent("接続元の情報を消す記録3");
    expect(result).toHaveTextContent("削除される使用量のファイル4");
    const [call] = sent(fetchMock);
    expect(call.body).toEqual({ dry_run: true, action: "anonymize" });
    expect(call.token).toBe("token-123");
  });

  it("runs for real only after the confirmation, with the chosen action", async () => {
    const fetchMock = stub({ "POST /admin/records/purge": () => jsonResponse(REAL) });
    await openTab("保持の処理");
    fireEvent.click(screen.getByLabelText(/削除する/));
    fireEvent.click(screen.getByRole("button", { name: "実際に実行する" }));
    const dialog = within(screen.getByRole("dialog"));
    expect(dialog.getByText("扱い: 削除")).toBeInTheDocument();
    const run = dialog.getByRole("button", { name: "実行する" });
    expect(run).toBeDisabled();
    expect(sent(fetchMock)).toHaveLength(0);
    fireEvent.click(dialog.getByLabelText("元に戻せないことを理解しました"));
    fireEvent.click(run);
    expect(await screen.findByText("保持の処理を実行しました")).toBeInTheDocument();
    expect(sent(fetchMock)[0].body).toEqual({ dry_run: false, action: "delete" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("says that only the installation administrator can run it (403)", async () => {
    stub({ "POST /admin/records/purge": () => jsonResponse({ detail: "forbidden" }, 403) });
    await openTab("保持の処理");
    fireEvent.click(screen.getByRole("button", { name: "試しに実行（何も変えません）" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("全体の管理者だけです");
  });
});
