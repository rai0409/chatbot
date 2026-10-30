import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SourcesPage } from "../pages/Sources";
import { jsonResponse } from "./fixtures";
import { FOLDER, HELD, HELD_PLAN, SHA, SHAREPOINT } from "./sourcesFixtures";

afterEach(cleanup);

type Entry = ((init: RequestInit) => Response) | Response | unknown;

/** A fetch that answers by "METHOD path"; the answer of a key may be a list that is used in turn (the last one repeats). */
function routes(table: Record<string, Entry | Entry[]>) {
  const used: Record<string, number> = {};
  const mock = vi.fn((url: string, init: RequestInit = {}) => {
    const key = `${(init.method ?? "GET").toUpperCase()} ${url}`;
    const found = table[key];
    const entry = Array.isArray(found) ? found[Math.min((used[key] = (used[key] ?? -1) + 1), found.length - 1)] : found;
    if (typeof entry === "function") return Promise.resolve((entry as (i: RequestInit) => Response)(init));
    if (entry instanceof Response) return Promise.resolve(entry.clone());
    return Promise.resolve(entry === undefined ? jsonResponse({ detail: "no route" }, 404) : jsonResponse(entry));
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}

function calls(mock: ReturnType<typeof vi.fn>, key: string) {
  return mock.mock.calls
    .filter(([url, init]) => `${((init as RequestInit)?.method ?? "GET").toUpperCase()} ${String(url)}` === key)
    .map(([, init]) => {
      const i = init as RequestInit;
      return { body: typeof i.body === "string" ? JSON.parse(i.body) : undefined, csrf: new Headers(i.headers).get("X-CSRF-Token") };
    });
}

const LIST = "GET /admin/sources/list";
const page = () => render(<SourcesPage />, { wrapper: MemoryRouter });
const rowOf = (id: string) => screen.getByRole("row", { name: new RegExp(`^${id}`) });

beforeEach(() => {
  document.cookie = "admin_csrf=token-123";
  vi.stubGlobal("location", { ...window.location, assign: vi.fn(), pathname: "/console/sources" });
});

describe("the sources page: list", () => {
  it("shows each source with its pending changes, the hold, and the state of a disabled integration", async () => {
    routes({ [LIST]: { sources: [FOLDER, HELD, SHAREPOINT] } });
    page();
    expect(screen.getByRole("status")).toHaveTextContent("読み込み中");
    expect(await screen.findByText("ソース数: 3")).toBeInTheDocument();
    expect(rowOf("policies")).toHaveTextContent("追加 2 / 更新 1 / 削除 0");
    expect(rowOf("manuals")).toHaveTextContent("保留: 削除の割合が大きすぎます");
    expect(rowOf("portal")).toHaveTextContent("（無効）");
    expect(rowOf("portal")).toHaveTextContent("（未走査）");
    expect(rowOf("portal")).toHaveTextContent("外部連携がオフです（コンソールのスイッチがオフです）");
  });

  it("says what to do when nothing is registered, and retries after an error", async () => {
    const mock = routes({ [LIST]: jsonResponse({ detail: "boom" }, 500) });
    page();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("ソースの一覧を読み込めませんでした");
    mock.mockImplementation(() => Promise.resolve(jsonResponse({ sources: [] })));
    fireEvent.click(within(alert).getByRole("button", { name: "再読み込み" }));
    expect(await screen.findByText(/ソースはまだ登録されていません/)).toBeInTheDocument();
  });
});

describe("the sources page: register", () => {
  it("checks the form, then sends a folder source as the older page did", async () => {
    const mock = routes({ [LIST]: { sources: [] }, "POST /admin/sources": FOLDER });
    page();
    await screen.findByText(/ソースはまだ/);
    fireEvent.click(screen.getByText("ソースを登録する"));
    fireEvent.click(screen.getByRole("button", { name: "登録" }));
    expect(screen.getByRole("alert")).toHaveTextContent("ID を入力してください");
    fireEvent.change(screen.getByLabelText("ID"), { target: { value: " policies " } });
    fireEvent.click(screen.getByRole("button", { name: "登録" }));
    expect(screen.getByRole("alert")).toHaveTextContent("フォルダのパスを入力してください");
    fireEvent.change(screen.getByLabelText("フォルダのパス"), { target: { value: "/data/policies" } });
    fireEvent.change(screen.getByLabelText("走査の間隔（分、0 = 手動）"), { target: { value: "60" } });
    fireEvent.click(screen.getByRole("button", { name: "登録" }));
    expect(await screen.findByText("登録しました")).toBeInTheDocument();
    const [sent] = calls(mock, "POST /admin/sources");
    expect(sent.body).toEqual({ source_id: "policies", tenant_id: "default", kind: "folder", schedule_minutes: 60, path: "/data/policies" });
    expect(sent.csrf).toBe("token-123");
  });

  it("sends a SharePoint source with its drive and folder, and shows the reason when the server refuses", async () => {
    const mock = routes({ [LIST]: { sources: [] }, "POST /admin/sources": jsonResponse({ detail: "source 'portal' already exists" }, 409) });
    page();
    await screen.findByText(/ソースはまだ/);
    fireEvent.click(screen.getByText("ソースを登録する"));
    fireEvent.change(screen.getByLabelText("ID"), { target: { value: "portal" } });
    fireEvent.change(screen.getByLabelText("種類"), { target: { value: "sharepoint" } });
    fireEvent.change(screen.getByLabelText("ドライブ ID"), { target: { value: "drive-1" } });
    fireEvent.change(screen.getByLabelText("ドライブ内のフォルダ"), { target: { value: "/Shared Documents/Policies" } });
    fireEvent.change(screen.getByLabelText("収集名（空なら src-ID）"), { target: { value: "my-coll" } });
    fireEvent.click(screen.getByRole("button", { name: "登録" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already exists");
    expect(calls(mock, "POST /admin/sources")[0].body).toEqual({
      source_id: "portal",
      tenant_id: "default",
      kind: "sharepoint",
      schedule_minutes: 0,
      options: { drive_id: "drive-1", folder: "/Shared Documents/Policies" },
      collection: "my-coll",
    });
  });
});

describe("the sources page: scan, plan, history", () => {
  it("shows an integration that is off as a calm message with the reason and a link to the system page", async () => {
    const mock = routes({
      [LIST]: { sources: [SHAREPOINT] },
      "POST /admin/sources/portal/scan": { error: "integration_disabled", reason: "env_not_allowed", run_id: "r1" },
    });
    page();
    fireEvent.click(await screen.findByRole("button", { name: "走査: portal" }));
    const note = await screen.findByText("外部連携がオフです", { selector: "strong" });
    const box = note.closest("[role=status]") as HTMLElement;
    expect(box).toHaveTextContent("環境で許可されていません（EXTERNAL_INTEGRATIONS）");
    expect(within(box).getByRole("link", { name: "システム状態を開く" })).toHaveAttribute("href", "/system");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument(); // not a failure of the source
    expect(calls(mock, "POST /admin/sources/portal/scan")[0].csrf).toBe("token-123");
  });

  it("scans a folder and reloads the list", async () => {
    const mock = routes({
      [LIST]: [{ sources: [FOLDER] }, { sources: [{ ...FOLDER, pending: { added: 5, modified: 0, deleted: 0 } }] }],
      "POST /admin/sources/policies/scan": { run_id: "r2", counts: { added: 5, modified: 0, deleted: 0 }, total_files: 15, skipped: {}, name_conflicts: 0 },
    });
    page();
    fireEvent.click(await screen.findByRole("button", { name: "走査: policies" }));
    expect(await screen.findByText("走査しました")).toBeInTheDocument();
    await waitFor(() => expect(rowOf("policies")).toHaveTextContent("追加 5"));
    expect(mock.mock.calls.filter(([u]) => u === "/admin/sources/list")).toHaveLength(2);
  });

  it("shows the plan with its fingerprint, the held warning and the excluded files", async () => {
    routes({ [LIST]: { sources: [HELD] }, "GET /admin/sources/manuals/plan": HELD_PLAN });
    page();
    fireEvent.click(await screen.findByRole("button", { name: "計画: manuals" }));
    const region = await screen.findByRole("region", { name: /計画: manuals/ });
    expect(region).toHaveTextContent(SHA);
    expect(within(region).getByRole("note")).toHaveTextContent("承認するまで反映されません");
    expect(region).toHaveTextContent("削除（12）");
  });

  it("lists the change ledger", async () => {
    routes({
      [LIST]: { sources: [FOLDER] },
      "GET /admin/sources/policies/changes": { entries: [{ seq: 3, at: "2026-01-05T09:00:00+00:00", kind: "apply", run_id: "r3", summary: { status: "applied" } }] },
    });
    page();
    fireEvent.click(await screen.findByRole("button", { name: "履歴: policies" }));
    const row = await screen.findByRole("row", { name: /apply/ });
    expect(row).toHaveTextContent("applied");
    expect(screen.getByText("履歴を表示しました")).toBeInTheDocument();
  });
});

describe("the sources page: apply, hold and approval", () => {
  it("applies a plan without deletions at once, with the CSRF token", async () => {
    const mock = routes({ [LIST]: { sources: [FOLDER] }, "POST /admin/sources/policies/apply": { status: "applied", ingested: 3, deleted: 0 } });
    page();
    fireEvent.click(await screen.findByRole("button", { name: "反映: policies" }));
    expect(await screen.findByText("反映: 反映しました")).toBeInTheDocument();
    const [sent] = calls(mock, "POST /admin/sources/policies/apply");
    expect(sent.body).toEqual({});
    expect(sent.csrf).toBe("token-123");
  });

  it("asks before applying a plan that deletes files", async () => {
    const mock = routes({
      [LIST]: { sources: [{ ...FOLDER, pending: { added: 0, modified: 0, deleted: 2 }, guard: { hold: false, reasons: [] } }] },
      "POST /admin/sources/policies/apply": { status: "applied" },
    });
    page();
    fireEvent.click(await screen.findByRole("button", { name: "反映: policies" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("2 件の削除を含みます");
    expect(calls(mock, "POST /admin/sources/policies/apply")).toHaveLength(0);
    fireEvent.click(within(dialog).getByRole("button", { name: "反映" }));
    expect(await screen.findByRole("status")).toHaveTextContent("反映: 反映しました");
    expect(calls(mock, "POST /admin/sources/policies/apply")).toHaveLength(1);
  });

  it("holds a large change, and approves the exact plan before applying it", async () => {
    const mock = routes({
      [LIST]: { sources: [{ ...HELD, pending: { added: 0, modified: 0, deleted: 12 }, guard: { hold: true, reasons: ["mass_delete"] } }] },
      "POST /admin/sources/manuals/apply": [
        { status: "held", reasons: ["mass_delete"], counts: { added: 0, modified: 0, deleted: 12 }, plan_sha256: SHA },
        { status: "applied", ingested: 0, deleted: 12 },
      ],
      "POST /admin/sources/manuals/approve": { approved: SHA, by: "admin", run_id: "r9" },
    });
    page();
    fireEvent.click(await screen.findByRole("button", { name: "反映: manuals" }));
    const confirmDelete = await screen.findByRole("dialog"); // 12 deletions: asked before the first apply
    fireEvent.click(within(confirmDelete).getByRole("button", { name: "反映" }));
    const dialog = await screen.findByText(/変更が大きいため保留されています（削除の割合が大きすぎます）/);
    expect(calls(mock, "POST /admin/sources/manuals/approve")).toHaveLength(0);
    fireEvent.click(within(dialog.closest("[role=dialog]") as HTMLElement).getByRole("button", { name: "承認して反映する" }));
    expect(await screen.findByText(/承認して反映しました/)).toBeInTheDocument();
    expect(calls(mock, "POST /admin/sources/manuals/approve")[0].body).toEqual({ plan_sha256: SHA });
    const applies = calls(mock, "POST /admin/sources/manuals/apply");
    expect(applies.map((a) => a.body)).toEqual([{}, { approval: SHA }]);
    expect(applies[1].csrf).toBe("token-123");
  });

  it("leaves a held plan held when the administrator declines", async () => {
    const mock = routes({
      [LIST]: { sources: [{ ...FOLDER, source_id: "manuals" }] },
      "POST /admin/sources/manuals/apply": { status: "held", reasons: ["too_many_changes"], plan_sha256: SHA },
    });
    page();
    fireEvent.click(await screen.findByRole("button", { name: "反映: manuals" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "キャンセル" }));
    expect(screen.getByText("保留のままです")).toBeInTheDocument();
    expect(calls(mock, "POST /admin/sources/manuals/approve")).toHaveLength(0);
  });

  it("says the plan changed when the approval is refused, and does not apply", async () => {
    const mock = routes({
      [LIST]: { sources: [{ ...FOLDER, source_id: "manuals" }] },
      "POST /admin/sources/manuals/apply": { status: "held", reasons: ["mass_delete"], plan_sha256: SHA },
      "POST /admin/sources/manuals/approve": jsonResponse({ detail: "the plan changed since it was shown; review the new plan" }, 400),
    });
    page();
    fireEvent.click(await screen.findByRole("button", { name: "反映: manuals" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "承認して反映する" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("承認できませんでした（the plan changed");
    expect(calls(mock, "POST /admin/sources/manuals/apply")).toHaveLength(1);
  });
});

describe("the sources page: switch, remove and permissions", () => {
  it("disables and enables a source", async () => {
    const mock = routes({ [LIST]: { sources: [FOLDER, SHAREPOINT] }, "POST /admin/sources/policies/disable": { ...FOLDER, enabled: false }, "POST /admin/sources/portal/enable": SHAREPOINT });
    page();
    fireEvent.click(await screen.findByRole("button", { name: "無効にする: policies" }));
    expect(await screen.findByText("更新しました")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "有効にする: portal" }));
    await waitFor(() => expect(calls(mock, "POST /admin/sources/portal/enable")).toHaveLength(1));
    expect(calls(mock, "POST /admin/sources/policies/disable")[0].csrf).toBe("token-123");
  });

  it("confirms before a source is unregistered", async () => {
    const mock = routes({ [LIST]: { sources: [FOLDER] }, "POST /admin/sources/policies/remove": { removed: "policies" } });
    page();
    fireEvent.click(await screen.findByRole("button", { name: "登録解除: policies" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("ソース policies の登録を解除しますか？（収集・履歴は残ります）");
    fireEvent.click(within(dialog).getByRole("button", { name: "キャンセル" }));
    expect(calls(mock, "POST /admin/sources/policies/remove")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "登録解除: policies" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "登録解除する" }));
    expect(await screen.findByText("登録を解除しました")).toBeInTheDocument();
  });

  it("explains a refusal for an administrator who may not use the source, and a change made by someone else", async () => {
    routes({
      [LIST]: { sources: [FOLDER] },
      "POST /admin/sources/policies/scan": [jsonResponse({ detail: "scope" }, 403), jsonResponse({ detail: "changed by someone else" }, 409)],
    });
    page();
    fireEvent.click(await screen.findByRole("button", { name: "走査: policies" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("この管理者には、このソースの操作が許可されていません。");
    fireEvent.click(screen.getByRole("button", { name: "走査: policies" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("他の人が先に変更しました"));
  });
});
