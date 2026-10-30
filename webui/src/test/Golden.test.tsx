import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GoldenPage } from "../pages/Golden";
import { jsonResponse } from "./fixtures";
import { CASE_A, CASE_B, LIST, router } from "./goldenFixtures";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

beforeEach(() => {
  document.cookie = "admin_csrf=token-123";
  vi.stubGlobal("location", { ...window.location, assign: vi.fn(), pathname: "/console/golden" });
});

type Routes = Parameters<typeof router>[0];

function stub(extra: Routes = {}) {
  const fetchMock = vi.fn(router({ "GET /admin/golden/default": () => jsonResponse(LIST), ...extra }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const posts = (fetchMock: ReturnType<typeof stub>, method: string) => fetchMock.mock.calls.filter(([, init]) => init?.method === method);

describe("the golden QA list", () => {
  it("loads the default tenant and shows each case, its state and where it came from", async () => {
    stub();
    render(<GoldenPage />);
    expect(screen.getByRole("status")).toHaveTextContent("読み込み中");
    const row = await screen.findByRole("row", { name: /gq-a1/ });
    expect(within(row).getByText("有効")).toBeInTheDocument();
    expect(within(row).getByText("記録から")).toBeInTheDocument();
    expect(screen.getByRole("row", { name: /gq-b2/ })).toHaveTextContent("無効");
    expect(screen.getByText("2 件（有効 1 件）")).toBeInTheDocument();
  });

  it("remembers the tenant and reads that tenant's cases", async () => {
    const fetchMock = stub({ "GET /admin/golden/sample-tenant": () => jsonResponse({ ...LIST, tenant_id: "sample-tenant", total: 0, enabled: 0, cases: [] }) });
    render(<GoldenPage />);
    await screen.findByRole("row", { name: /gq-a1/ });
    fireEvent.change(screen.getByLabelText("テナント"), { target: { value: "sample-tenant" } });
    fireEvent.click(screen.getByRole("button", { name: "読み込む" }));
    expect(await screen.findByText(/ゴールデン QA がまだありません/)).toBeInTheDocument();
    expect(fetchMock.mock.calls[1][0]).toBe("/admin/golden/sample-tenant");
    expect(window.localStorage.getItem("console.goldenTenant")).toBe("sample-tenant");
  });

  it("filters by enabled or disabled", async () => {
    stub();
    render(<GoldenPage />);
    await screen.findByRole("row", { name: /gq-a1/ });
    fireEvent.change(screen.getByLabelText("表示"), { target: { value: "disabled" } });
    expect(screen.queryByRole("row", { name: /gq-a1/ })).toBeNull();
    expect(screen.getByRole("row", { name: /gq-b2/ })).toBeInTheDocument();
  });

  it("shows an error with a retry button", async () => {
    let calls = 0;
    stub({ "GET /admin/golden/default": () => (calls++ === 0 ? jsonResponse({ detail: "x" }, 500) : jsonResponse(LIST)) });
    render(<GoldenPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("読み込めませんでした");
    fireEvent.click(screen.getByRole("button", { name: "再読み込み" }));
    expect(await screen.findByRole("row", { name: /gq-a1/ })).toBeInTheDocument();
  });
});

describe("creating and editing", () => {
  it("creates a case: one term per line, tags by comma, with the CSRF token", async () => {
    const fetchMock = stub({ "POST /admin/golden/default": () => jsonResponse({ ...CASE_A, case_id: "gq-new" }) });
    render(<GoldenPage />);
    fireEvent.click(await screen.findByRole("button", { name: "新しく作る" }));
    fireEvent.change(screen.getByLabelText("質問"), { target: { value: " 通勤手当の上限は " } });
    fireEvent.change(screen.getByLabelText("含めたい言葉（すべて）"), { target: { value: "5万円\n\n 月額 " } });
    fireEvent.change(screen.getByLabelText("含めてはいけない言葉"), { target: { value: "無制限" } });
    fireEvent.change(screen.getByLabelText("タグ"), { target: { value: "手当, 交通" } });
    fireEvent.click(screen.getByLabelText(/出典（根拠の文書）が付いていること/));
    fireEvent.change(screen.getByLabelText("優先度"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByText("作りました（ID: gq-new）")).toBeInTheDocument();
    const [url, init] = posts(fetchMock, "POST")[0];
    expect(url).toBe("/admin/golden/default");
    expect(JSON.parse(init!.body as string)).toEqual({
      question: "通勤手当の上限は",
      expected_all: ["5万円", "月額"],
      expected_any: [],
      forbidden_any: ["無制限"],
      reference: "",
      expected_question_type: "",
      group: "",
      tags: ["手当", "交通"],
      expected_citation: true,
      enabled: true,
      priority: 3,
    });
    expect(new Headers(init!.headers).get("X-CSRF-Token")).toBe("token-123");
    expect(screen.queryByRole("form")).toBeNull();
  });

  it("explains what each field checks and refuses a case that checks nothing, without calling the server", async () => {
    const fetchMock = stub();
    render(<GoldenPage />);
    fireEvent.click(await screen.findByRole("button", { name: "新しく作る" }));
    expect(screen.getByText(/回答に、ここの言葉がすべて入っていると合格です/)).toBeInTheDocument();
    expect(screen.getByText(/1つの質問は、必ず何かを調べるようにします/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(screen.getByRole("alert")).toHaveTextContent("質問を入力してください");
    fireEvent.change(screen.getByLabelText("質問"), { target: { value: "何か" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(screen.getByRole("alert")).toHaveTextContent("調べる内容がありません");
    expect(posts(fetchMock, "POST")).toHaveLength(0);
  });

  it("shows the server's validation errors (400) next to the form, and keeps what was typed", async () => {
    stub({ "POST /admin/golden/default": () => jsonResponse({ detail: "gq-x: 'priority' must be an integer; gq-x: another problem" }, 400) });
    render(<GoldenPage />);
    fireEvent.click(await screen.findByRole("button", { name: "新しく作る" }));
    fireEvent.change(screen.getByLabelText("ID"), { target: { value: "gq-x" } });
    fireEvent.change(screen.getByLabelText("質問"), { target: { value: "質問です" } });
    fireEvent.change(screen.getByLabelText("基準の回答"), { target: { value: "答え" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    const alert = await screen.findByRole("alert");
    expect(within(alert).getAllByRole("listitem")).toHaveLength(2);
    expect(alert).toHaveTextContent("'priority' must be an integer");
    expect(screen.getByLabelText("質問")).toHaveValue("質問です");
  });

  it("edits a case, sending the loaded updated_at, and no case id field", async () => {
    const fetchMock = stub({ "PUT /admin/golden/default/gq-a1": () => jsonResponse(CASE_A) });
    render(<GoldenPage />);
    fireEvent.click(await screen.findByRole("button", { name: "gq-a1 を編集" }));
    expect(screen.queryByLabelText("ID")).toBeNull();
    expect(screen.getByLabelText("含めたい言葉（すべて）")).toHaveValue("10日");
    fireEvent.change(screen.getByLabelText("質問"), { target: { value: "有給は何日" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByText("変更しました")).toBeInTheDocument();
    const [, init] = posts(fetchMock, "PUT")[0];
    const body = JSON.parse(init!.body as string);
    expect(body.expected_updated_at).toBe(CASE_A.updated_at);
    expect(body.question).toBe("有給は何日");
    expect(body.expected_all).toEqual(["10日"]);
    expect(new Headers(init!.headers).get("X-CSRF-Token")).toBe("token-123");
  });

  it("says someone else changed the case (409) and offers to reload", async () => {
    const fetchMock = stub({ "PUT /admin/golden/default/gq-a1": () => jsonResponse({ detail: "the case was changed by someone else; reload it" }, 409) });
    render(<GoldenPage />);
    fireEvent.click(await screen.findByRole("button", { name: "gq-a1 を編集" }));
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("他の人が先に変更しました");
    fireEvent.click(screen.getByRole("button", { name: "最新の内容を読み込む" }));
    await waitFor(() => expect(fetchMock.mock.calls.filter(([, init]) => !init?.method || init.method === "GET")).toHaveLength(2));
    expect(screen.queryByLabelText("質問")).toBeNull();
  });
});

describe("enable, disable, delete and export", () => {
  it("turns a case off and on with the loaded updated_at", async () => {
    const fetchMock = stub({
      "PUT /admin/golden/default/gq-a1": () => jsonResponse({ ...CASE_A, enabled: false }),
      "PUT /admin/golden/default/gq-b2": () => jsonResponse({ ...CASE_B, enabled: true }),
    });
    render(<GoldenPage />);
    fireEvent.click(await screen.findByRole("button", { name: "gq-a1 を無効にする" }));
    await screen.findByText("変更しました");
    fireEvent.click(screen.getByRole("button", { name: "gq-b2 を有効にする" }));
    await waitFor(() => expect(posts(fetchMock, "PUT")).toHaveLength(2));
    const bodies = posts(fetchMock, "PUT").map(([, init]) => JSON.parse(init!.body as string));
    expect(bodies).toEqual([
      { enabled: false, expected_updated_at: CASE_A.updated_at },
      { enabled: true, expected_updated_at: CASE_B.updated_at },
    ]);
  });

  it("shows a plain sentence when a change is not allowed (403)", async () => {
    stub({ "PUT /admin/golden/default/gq-a1": () => jsonResponse({ detail: "forbidden" }, 403) });
    render(<GoldenPage />);
    fireEvent.click(await screen.findByRole("button", { name: "gq-a1 を無効にする" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("権限がありません");
  });

  it("deletes only after a confirmation that explains where the case goes", async () => {
    const fetchMock = stub({ "DELETE /admin/golden/default/gq-b2": () => jsonResponse({ ok: true }) });
    render(<GoldenPage />);
    fireEvent.click(await screen.findByRole("button", { name: "gq-b2 を削除" }));
    const dialog = within(screen.getByRole("dialog"));
    expect(dialog.getByText(/削除済み」の保管場所に移されます/)).toBeInTheDocument();
    expect(posts(fetchMock, "DELETE")).toHaveLength(0);
    fireEvent.click(dialog.getByRole("button", { name: "キャンセル" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "gq-b2 を削除" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "削除する" }));
    expect(await screen.findByText("削除しました")).toBeInTheDocument();
    const [, init] = posts(fetchMock, "DELETE")[0];
    expect(new Headers(init!.headers).get("X-CSRF-Token")).toBe("token-123");
  });

  it("downloads the exported JSON through a temporary link", async () => {
    const exported = { tenant_id: "default", golden_qa: [{ case_id: "gq-a1", question: "q" }] };
    stub({ "GET /admin/golden/default/export": () => jsonResponse(exported) });
    const create = vi.fn(() => "blob:sample");
    const revoke = vi.fn();
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke }));
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    render(<GoldenPage />);
    fireEvent.click(await screen.findByRole("button", { name: "書き出す（JSON）" }));
    expect(await screen.findByText("書き出しました")).toBeInTheDocument();
    expect(create).toHaveBeenCalledTimes(1);
    const blob = (create.mock.calls[0] as unknown as [Blob])[0];
    expect(JSON.parse(await blob.text())).toEqual(exported);
    expect(click).toHaveBeenCalledTimes(1);
    expect(revoke).toHaveBeenCalledWith("blob:sample");
    click.mockRestore();
  });
});
