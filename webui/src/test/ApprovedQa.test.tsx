import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApprovedQaPage } from "../pages/ApprovedQa";
import { HEALTH, HISTORY, LIST, RECORD } from "./approvedQaFixtures";
import { jsonResponse } from "./fixtures";

afterEach(cleanup);

type Handler = (init: RequestInit, url: string) => Response;

function routes(table: Record<string, Handler | unknown>) {
  const all: Record<string, Handler | unknown> = {
    "GET /admin/approved-qa": LIST,
    "GET /admin/approved-qa/health": HEALTH,
    "GET /admin/approved-qa/record": { record: RECORD, history: HISTORY },
    ...table,
  };
  const mock = vi.fn((url: string, init: RequestInit = {}) => {
    const key = `${(init.method ?? "GET").toUpperCase()} ${url.split("?")[0]}`;
    const entry = all[key];
    if (typeof entry === "function") return Promise.resolve((entry as Handler)(init, url));
    return Promise.resolve(entry === undefined ? jsonResponse({ detail: "no route" }, 404) : jsonResponse(entry));
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}

const sent = (mock: ReturnType<typeof vi.fn>, key: string) =>
  mock.mock.calls.filter(([url, init]) => `${((init as RequestInit)?.method ?? "GET").toUpperCase()} ${String(url).split("?")[0]}` === key).map(([, init]) => JSON.parse(String((init as RequestInit).body)));

beforeEach(() => {
  document.cookie = "admin_csrf=token-123";
  window.sessionStorage.clear();
  vi.stubGlobal("location", { ...window.location, assign: vi.fn(), pathname: "/console/approved-qa" });
});

describe("the approved QA page", () => {
  it("lists the QA with status, counts and the actions that fit each state", async () => {
    routes({});
    render(<ApprovedQaPage />);
    expect(await screen.findByText(/下書き 1 ／ 承認済み 1 ／ 却下 0\s*該当: 2 件/)).toBeInTheDocument();
    const draft = screen.getByRole("row", { name: /申請期限はいつですか/ });
    expect(draft).toHaveTextContent("下書き");
    expect(within(draft).getByRole("button", { name: /承認する/ })).toBeInTheDocument();
    expect(within(draft).getByRole("button", { name: /削除/ })).toBeInTheDocument();
    const approved = screen.getByRole("row", { name: /解約の方法/ });
    expect(within(approved).queryByRole("button", { name: /^削除/ })).toBeNull(); // an approved QA is taken back first
    expect(within(approved).getByRole("button", { name: /下書きに戻す/ })).toBeInTheDocument();
  });

  it("warns when chat does not use approved QA and when a second approver is required", async () => {
    routes({ "GET /admin/approved-qa": { ...LIST, enabled: false, require_approver: true } });
    render(<ApprovedQaPage />);
    expect(await screen.findByRole("note")).toHaveTextContent("チャットは、いま承認済み QA を使っていません");
    expect(screen.getByText("承認は、最後に編集した人とは別の人が行います。")).toBeInTheDocument();
  });

  it("shows file problems to the administrator who may see them", async () => {
    routes({ "GET /admin/approved-qa/health": { ...HEALTH, errors: ["duplicate normalized_question for tenant_id=tenant_a: x"], unreadable_lines: 2 } });
    render(<ApprovedQaPage />);
    expect(await screen.findByText("読み取れない行が 2 行あります。直すまで、画面からの変更はできません。")).toBeInTheDocument();
    expect(screen.getByText("ファイルに 1 件の問題があります。内容を確認してください。")).toBeInTheDocument();
  });

  it("tells the administrator when the place to keep approved QA is not set", async () => {
    routes({ "GET /admin/approved-qa/health": { ...HEALTH, path_is_default: true } });
    render(<ApprovedQaPage />);
    expect(await screen.findByText(/APPROVED_QA_PATH）が未設定です/)).toBeInTheDocument();
  });

  it("filters by tenant, status and text", async () => {
    const mock = routes({});
    render(<ApprovedQaPage />);
    await screen.findByText(/該当: 2 件/);
    fireEvent.change(screen.getByLabelText("テナント"), { target: { value: "tenant_a" } });
    fireEvent.change(screen.getByLabelText("状態"), { target: { value: "approved" } });
    fireEvent.change(screen.getByLabelText("検索（質問・回答・分類）"), { target: { value: "解約" } });
    fireEvent.click(screen.getByRole("button", { name: "読み込み" }));
    await waitFor(() => expect(mock.mock.calls.some(([u]) => String(u).includes("tenant_id=tenant_a") && String(u).includes("status=approved") && String(u).includes("q=%E8%A7%A3%E7%B4%84"))).toBe(true));
  });

  it("creates a QA after checking the form, with a reason and a name", async () => {
    const mock = routes({ "POST /admin/approved-qa/save": { ...RECORD, qa_id: "qa-9" } });
    render(<ApprovedQaPage />);
    fireEvent.click(await screen.findByRole("button", { name: "新規作成" }));
    const form = screen.getByRole("region", { name: "QA を作る" });
    fireEvent.click(within(form).getByRole("button", { name: "保存" }));
    expect(await within(form).findByText("質問と回答を入力してください。")).toBeInTheDocument();
    fireEvent.change(within(form).getByLabelText("テナント"), { target: { value: "tenant_a" } });
    fireEvent.change(within(form).getByLabelText("質問"), { target: { value: "新しい質問です" } });
    fireEvent.change(within(form).getByLabelText("回答"), { target: { value: "新しい回答です" } });
    fireEvent.change(within(form).getByLabelText("言い換え（1 行に 1 つ）"), { target: { value: "別の聞き方\n  \nもう一つ" } });
    fireEvent.change(within(form).getByLabelText("分類（カンマ区切り）"), { target: { value: "手続き, 料金" } });
    fireEvent.change(within(form).getByLabelText("文書名"), { target: { value: "manual.pdf" } });
    fireEvent.change(within(form).getByLabelText("ページ（カンマ区切り）"), { target: { value: "2, 3" } });
    fireEvent.click(within(form).getByRole("button", { name: "保存" }));
    expect(await within(form).findByText("理由と名前を入力してください。")).toBeInTheDocument();
    fireEvent.change(within(form).getByLabelText("理由（必須）"), { target: { value: "新規登録" } });
    fireEvent.change(within(form).getByLabelText("あなたの名前（必須）"), { target: { value: "山田太郎" } });
    fireEvent.click(within(form).getByRole("button", { name: "保存" }));
    expect(await screen.findByText("保存しました。")).toBeInTheDocument();
    expect(sent(mock, "POST /admin/approved-qa/save")[0]).toEqual({
      tenant_id: "tenant_a", question: "新しい質問です", approved_answer: "新しい回答です", aliases: ["別の聞き方", "もう一つ"], tags: ["手続き", "料金"],
      citations: [{ source_doc: "manual.pdf", source_pages: [2, 3] }], doc_version: null, reason: "新規登録", actor: "山田太郎",
    });
  });

  it("refuses page numbers that are not whole numbers from 1", async () => {
    const mock = routes({});
    render(<ApprovedQaPage />);
    fireEvent.click(await screen.findByRole("button", { name: "新規作成" }));
    const form = screen.getByRole("region", { name: "QA を作る" });
    fireEvent.change(within(form).getByLabelText("質問"), { target: { value: "q" } });
    fireEvent.change(within(form).getByLabelText("回答"), { target: { value: "a" } });
    fireEvent.change(within(form).getByLabelText("文書名"), { target: { value: "a.pdf" } });
    fireEvent.change(within(form).getByLabelText("ページ（カンマ区切り）"), { target: { value: "0" } });
    fireEvent.change(within(form).getByLabelText("理由（必須）"), { target: { value: "x" } });
    fireEvent.change(within(form).getByLabelText("あなたの名前（必須）"), { target: { value: "山田太郎" } });
    fireEvent.click(within(form).getByRole("button", { name: "保存" }));
    expect(await within(form).findByText("ページは 1 以上の整数で入力してください。")).toBeInTheDocument();
    expect(sent(mock, "POST /admin/approved-qa/save")).toHaveLength(0);
  });

  it("opens a QA with its citations and history, and says when an edit took it back to draft", async () => {
    const mock = routes({ "POST /admin/approved-qa/save": { ...RECORD, reverted_to_draft: true } });
    render(<ApprovedQaPage />);
    fireEvent.click(await screen.findByRole("button", { name: /編集: 申請期限はいつですか/ }));
    const form = await screen.findByRole("region", { name: "QA を編集" });
    expect(within(form).getByLabelText("質問")).toHaveValue("申請期限はいつですか？");
    expect(within(form).getByLabelText("言い換え（1 行に 1 つ）")).toHaveValue("期限は？");
    expect(within(form).getByLabelText("文書名")).toHaveValue("manual.pdf");
    expect(within(form).getByLabelText("テナント")).toBeDisabled();
    expect(within(form).getByRole("cell", { name: "新規" })).toBeInTheDocument();
    fireEvent.change(within(form).getByLabelText("回答"), { target: { value: "毎月25日です。" } });
    fireEvent.change(within(form).getByLabelText("理由（必須）"), { target: { value: "期限が変わった" } });
    fireEvent.change(within(form).getByLabelText("あなたの名前（必須）"), { target: { value: "山田太郎" } });
    fireEvent.click(within(form).getByRole("button", { name: "保存" }));
    expect(await screen.findByText(/下書きに戻りました/)).toBeInTheDocument();
    expect(sent(mock, "POST /admin/approved-qa/save")[0]).toMatchObject({ qa_id: "qa-1", expected_updated_at: RECORD.updated_at, approved_answer: "毎月25日です。" });
  });

  it("approves with a reason and a name, and shows the server's reason when it refuses", async () => {
    let attempt = 0;
    const mock = routes({
      "POST /admin/approved-qa/status": () => (++attempt === 1 ? jsonResponse({ detail: "an approved record needs at least one valid citation" }, 400) : jsonResponse(RECORD)),
    });
    render(<ApprovedQaPage />);
    fireEvent.click(await screen.findByRole("button", { name: /承認する: 申請期限/ }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("この回答を、チャットが利用者へ返すようになります。");
    fireEvent.click(within(dialog).getByRole("button", { name: "保存" }));
    expect(await within(dialog).findByText("理由と名前を入力してください。")).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText("理由（必須）"), { target: { value: "内容を確認した" } });
    fireEvent.change(within(dialog).getByLabelText("あなたの名前（必須）"), { target: { value: "佐藤次郎" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "保存" }));
    expect(await screen.findByText(/できませんでした: an approved record needs at least one valid citation/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /承認する: 申請期限/ }));
    const again = screen.getByRole("dialog");
    fireEvent.change(within(again).getByLabelText("理由（必須）"), { target: { value: "出典を足した" } });
    fireEvent.click(within(again).getByRole("button", { name: "保存" })); // the name is remembered
    expect(await screen.findByText("「承認済み」にしました。")).toBeInTheDocument();
    expect(sent(mock, "POST /admin/approved-qa/status")[1]).toEqual({ tenant_id: "tenant_a", qa_id: "qa-1", status: "approved", reason: "出典を足した", actor: "佐藤次郎" });
  });

  it("deletes a draft after a confirmation with a reason", async () => {
    const mock = routes({ "POST /admin/approved-qa/delete": { ok: true } });
    render(<ApprovedQaPage />);
    fireEvent.click(await screen.findByRole("button", { name: /削除: 申請期限/ }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("削除は元に戻せません");
    fireEvent.change(within(dialog).getByLabelText("理由（必須）"), { target: { value: "重複のため" } });
    fireEvent.change(within(dialog).getByLabelText("あなたの名前（必須）"), { target: { value: "山田太郎" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "削除" }));
    expect(await screen.findByText("削除しました。")).toBeInTheDocument();
    expect(sent(mock, "POST /admin/approved-qa/delete")[0]).toEqual({ tenant_id: "tenant_a", qa_id: "qa-1", reason: "重複のため", actor: "山田太郎" });
  });

  it("says when someone else changed the record, or when the tenant is not allowed", async () => {
    routes({ "POST /admin/approved-qa/status": () => jsonResponse({ detail: "x" }, 409) });
    render(<ApprovedQaPage />);
    fireEvent.click(await screen.findByRole("button", { name: /承認する: 申請期限/ }));
    let dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("理由（必須）"), { target: { value: "x" } });
    fireEvent.change(within(dialog).getByLabelText("あなたの名前（必須）"), { target: { value: "佐藤次郎" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "保存" }));
    expect(await screen.findByText("他の人が先に変更しました。読み込み直してください。")).toBeInTheDocument();
    cleanup();
    routes({ "POST /admin/approved-qa/status": () => jsonResponse({ detail: "x" }, 403) });
    render(<ApprovedQaPage />);
    fireEvent.click(await screen.findByRole("button", { name: /承認する: 申請期限/ }));
    dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("理由（必須）"), { target: { value: "x" } });
    fireEvent.change(within(dialog).getByLabelText("あなたの名前（必須）"), { target: { value: "佐藤次郎" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "保存" }));
    expect(await screen.findByText("このテナントの QA を変更する権限がありません。")).toBeInTheDocument();
  });

  it("previews a CSV import without writing, then applies it", async () => {
    const result = (applied: boolean) => ({
      applied, counts: { create: 1, update: 0, skip: 1, error: 1 },
      rows: [
        { line: 2, action: "create", message: "", question: "新しい質問" },
        { line: 3, action: "skip", message: "同じ質問が既にあります", question: "既存の質問" },
        { line: 4, action: "error", message: "citation source document is not valid", question: "壊れた行" },
      ],
    });
    const mock = routes({ "POST /admin/approved-qa/import": (init: RequestInit) => jsonResponse(result(JSON.parse(String(init.body)).apply)) });
    render(<ApprovedQaPage />);
    fireEvent.click(await screen.findByRole("button", { name: "CSV から取り込む" }));
    const panel = screen.getByRole("region", { name: "CSV から取り込む" });
    fireEvent.click(within(panel).getByRole("button", { name: "内容を確認（書き込まない）" }));
    expect(await within(panel).findByText("CSV ファイルを選んでください。")).toBeInTheDocument();
    const file = new File(["質問,正解回答\n"], "qa.csv", { type: "text/csv" });
    fireEvent.change(within(panel).getByLabelText("CSV ファイル"), { target: { files: [file] } });
    await within(panel).findByText("qa.csv");
    fireEvent.click(within(panel).getByRole("button", { name: "内容を確認（書き込まない）" }));
    expect(await within(panel).findByText(/確認だけです（まだ書き込んでいません）。 作成 1 ／ 更新 0 ／ 飛ばし 1 ／ エラー 1/)).toBeInTheDocument();
    expect(within(panel).getByRole("row", { name: /4 壊れた行 エラー citation source document/ })).toBeInTheDocument();
    const first = sent(mock, "POST /admin/approved-qa/import")[0];
    expect(first).toMatchObject({ apply: false, tenant_id: "default", on_conflict: "skip" });
    expect(typeof first.content_base64).toBe("string");
    const decoded = new TextDecoder().decode(Uint8Array.from(atob(first.content_base64), (c) => c.charCodeAt(0)));
    expect(decoded).toContain("質問"); // the file goes as bytes (base64), so a Shift_JIS file keeps its encoding for the server
    fireEvent.click(within(panel).getByRole("button", { name: "取り込む" }));
    expect(await within(panel).findByText("理由と名前を入力してください。")).toBeInTheDocument();
    fireEvent.change(within(panel).getByLabelText("理由（必須）"), { target: { value: "Excel から取り込み" } });
    fireEvent.change(within(panel).getByLabelText("あなたの名前（必須）"), { target: { value: "山田太郎" } });
    fireEvent.click(within(panel).getByRole("button", { name: "取り込む" }));
    expect(await within(panel).findByText(/取り込みました。/)).toBeInTheDocument();
    expect(sent(mock, "POST /admin/approved-qa/import")[1]).toMatchObject({ apply: true, reason: "Excel から取り込み", actor: "山田太郎" });
  });

  it("offers the export and the template as plain downloads", async () => {
    routes({});
    render(<ApprovedQaPage />);
    expect(await screen.findByRole("link", { name: "JSONL を書き出す" })).toHaveAttribute("href", "/admin/approved-qa/export");
    expect(screen.getAllByRole("link", { name: "取込用テンプレート（CSV）" })[0]).toHaveAttribute("href", "/admin/approved-qa/template");
  });

  it("shows an error with a retry when the list cannot be loaded", async () => {
    routes({ "GET /admin/approved-qa": () => jsonResponse({ detail: "x" }, 500) });
    render(<ApprovedQaPage />);
    expect(await screen.findAllByText("承認済み QA を取得できませんでした。")).not.toHaveLength(0);
  });
});
