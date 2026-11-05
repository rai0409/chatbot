import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { IngestionPage } from "../pages/Ingestion";
import { jsonResponse } from "./fixtures";
import { JOBS, JOB_DETAIL_DONE, JOB_DETAIL_OK, UPLOAD_RESULT } from "./ingestionFixtures";

afterEach(cleanup);

type Handler = (init: RequestInit) => Response;

/** A fetch that answers by "METHOD path" (the query string is ignored in the key); anything else is a 404. */
function routes(table: Record<string, Handler | Response | unknown>) {
  const mock = vi.fn((url: string, init: RequestInit = {}) => {
    const key = `${(init.method ?? "GET").toUpperCase()} ${url.split("?")[0]}`;
    const entry = table[key];
    if (typeof entry === "function") return Promise.resolve((entry as Handler)(init));
    if (entry instanceof Response) return Promise.resolve(entry.clone());
    return Promise.resolve(entry === undefined ? jsonResponse({ detail: "no route" }, 404) : jsonResponse(entry));
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}

function call(mock: ReturnType<typeof vi.fn>, key: string) {
  const found = mock.mock.calls.find(([url, init]) => `${((init as RequestInit)?.method ?? "GET").toUpperCase()} ${String(url).split("?")[0]}` === key);
  if (!found) return null;
  const init = found[1] as RequestInit;
  return { url: String(found[0]), init, body: typeof init.body === "string" ? JSON.parse(init.body) : init.body, csrf: new Headers(init.headers).get("X-CSRF-Token") };
}

beforeEach(() => {
  document.cookie = "admin_csrf=token-123";
  vi.stubGlobal("location", { ...window.location, assign: vi.fn(), pathname: "/console/ingestion" });
});

const field = (name: RegExp | string) => screen.getByLabelText(name);

describe("the ingestion page: jobs", () => {
  it("shows the job history with readable states", async () => {
    routes({ "GET /admin/ingestion/jobs": { jobs: JOBS } });
    render(<IngestionPage />);
    expect(await screen.findByRole("row", { name: /aaaaaaaa/ })).toHaveTextContent("問題なし");
    expect(screen.getByRole("row", { name: /bbbbbbbb/ })).toHaveTextContent("実行中");
    expect(screen.getByRole("row", { name: /aaaaaaaa/ })).toHaveTextContent("ドライラン");
  });

  it("says what to do when there is no job, and offers a retry when the list cannot be read", async () => {
    const mock = routes({ "GET /admin/ingestion/jobs": jsonResponse({ detail: "boom" }, 500) });
    render(<IngestionPage />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("ジョブ履歴を取得できませんでした");
    mock.mockImplementation(() => Promise.resolve(jsonResponse({ jobs: [] })));
    fireEvent.click(within(alert).getByRole("button", { name: "再読み込み" }));
    expect(await screen.findByText(/ジョブはありません/)).toBeInTheDocument();
  });

  it("opens the detail of a job", async () => {
    routes({ "GET /admin/ingestion/jobs": { jobs: JOBS }, "GET /admin/ingestion/jobs/aaaaaaaa1111": JOB_DETAIL_OK });
    render(<IngestionPage />);
    fireEvent.click(await screen.findByRole("button", { name: "詳細: aaaaaaaa" }));
    const region = await screen.findByRole("region", { name: "ジョブの詳細" });
    expect(region).toHaveTextContent("完了しました。");
  });
});

describe("the ingestion page: dry run of JSONL", () => {
  it("needs a path, then sends the inputs and the CSRF token", async () => {
    const mock = routes({ "GET /admin/ingestion/jobs": { jobs: [] }, "POST /admin/ingestion/dry-run": { ...JOB_DETAIL_OK } });
    render(<IngestionPage />);
    await screen.findByText(/ジョブはありません/);
    fireEvent.click(screen.getByRole("button", { name: "ドライラン検証を実行" }));
    expect(screen.getByRole("alert")).toHaveTextContent("入力 JSONL パスを入力してください");
    expect(call(mock, "POST /admin/ingestion/dry-run")).toBeNull();

    fireEvent.change(field(/入力 JSONL パス/), { target: { value: " a.jsonl \n\n b.jsonl " } });
    fireEvent.change(field(/想定テナントID/), { target: { value: "tenant_a" } });
    fireEvent.click(screen.getByRole("button", { name: "ドライラン検証を実行" }));
    expect(await screen.findByRole("region", { name: "ドライラン検証の結果" })).toHaveTextContent("完了しました。");
    const sent = call(mock, "POST /admin/ingestion/dry-run")!;
    expect(sent.body).toEqual({ inputs: ["a.jsonl", "b.jsonl"], expected_tenant: "tenant_a", collection: null });
    expect(sent.csrf).toBe("token-123");
  });
});

describe("the ingestion page: documents", () => {
  it("uploads a file with the CSRF token and uses its server path for the check", async () => {
    const mock = routes({
      "GET /admin/ingestion/jobs": { jobs: [] },
      "PUT /admin/ingestion/upload": UPLOAD_RESULT,
      "POST /admin/ingestion/raw-documents": { status: "ok", ok: true, mode: "dry_run" },
    });
    const { container } = render(<IngestionPage />);
    await screen.findByText(/ジョブはありません/);
    fireEvent.change(field("対象テナントID（必須）"), { target: { value: "tenant_a" } });
    fireEvent.change(field("ステージングコレクション（必須・非本番のみ）"), { target: { value: "staging_v1" } });
    const input = container.querySelector("input[type=file]") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["%PDF-1.4"], "manual.pdf", { type: "application/pdf" }), new File(["x"], "memo.txt")] } });
    expect(await screen.findByText("4 KB")).toBeInTheDocument();
    expect(screen.getByText("対応していない形式です")).toBeInTheDocument();
    const upload = mock.mock.calls.filter(([, init]) => (init as RequestInit)?.method === "PUT");
    expect(upload).toHaveLength(1); // the unsupported file is never sent
    expect(String(upload[0][0])).toBe("/admin/ingestion/upload?filename=manual.pdf&tenant=tenant_a");
    expect(new Headers((upload[0][1] as RequestInit).headers).get("X-CSRF-Token")).toBe("token-123");

    fireEvent.click(screen.getByRole("button", { name: "検証／取込を実行" }));
    await screen.findByRole("region", { name: "検証／取込の結果" });
    expect(call(mock, "POST /admin/ingestion/raw-documents")!.body).toEqual({
      inputs: [UPLOAD_RESULT.path],
      expected_tenant: "tenant_a",
      collection: "staging_v1",
      execute: false,
      async_job: false,
    });
  });

  it("asks for documents, tenant and collection before anything is sent", async () => {
    const mock = routes({ "GET /admin/ingestion/jobs": { jobs: [] } });
    render(<IngestionPage />);
    await screen.findByText(/ジョブはありません/);
    const run = () => fireEvent.click(screen.getByRole("button", { name: "検証／取込を実行" }));
    run();
    expect(screen.getByRole("alert")).toHaveTextContent("文書を選んでください");
    fireEvent.change(field(/サーバー上のパス/), { target: { value: "docs/a.docx" } });
    run();
    expect(screen.getByRole("alert")).toHaveTextContent("対象テナントIDを入力してください");
    fireEvent.change(field("対象テナントID（必須）"), { target: { value: "tenant_a" } });
    run();
    expect(screen.getByRole("alert")).toHaveTextContent("ステージングコレクションを入力してください");
    expect(call(mock, "POST /admin/ingestion/raw-documents")).toBeNull();
  });

  it("confirms the real ingestion, runs it in the background and follows the job to its end", async () => {
    const mock = routes({
      "GET /admin/ingestion/jobs": { jobs: [] },
      "POST /admin/ingestion/raw-documents": { async: true, job_id: "bbbbbbbb2222", status: "queued" },
      "GET /admin/ingestion/jobs/bbbbbbbb2222": JOB_DETAIL_DONE,
    });
    render(<IngestionPage />);
    await screen.findByText(/ジョブはありません/);
    fireEvent.change(field(/サーバー上のパス/), { target: { value: "docs/a.docx" } });
    fireEvent.change(field("対象テナントID（必須）"), { target: { value: "tenant_a" } });
    fireEvent.change(field("ステージングコレクション（必須・非本番のみ）"), { target: { value: "staging_v1" } });
    fireEvent.click(field(/実取込する/));
    fireEvent.click(field(/バックグラウンドで実行する/));
    fireEvent.click(screen.getByRole("button", { name: "検証／取込を実行" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("ステージングコレクションへ取り込みます。よろしいですか？");
    expect(call(mock, "POST /admin/ingestion/raw-documents")).toBeNull(); // not before the confirmation
    fireEvent.click(within(dialog).getByRole("button", { name: "実行" }));
    await waitFor(() => expect(screen.getByRole("region", { name: "ジョブの詳細" })).toHaveTextContent("完了しました。"));
    const sent = call(mock, "POST /admin/ingestion/raw-documents")!;
    expect(sent.body).toMatchObject({ execute: true, async_job: true, inputs: ["docs/a.docx"] });
    expect(sent.csrf).toBe("token-123");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("shows the refusal of the server as a message", async () => {
    routes({
      "GET /admin/ingestion/jobs": { jobs: [] },
      "POST /admin/ingestion/raw-documents": jsonResponse({ detail: "refusing production/default collection" }, 400),
    });
    render(<IngestionPage />);
    await screen.findByText(/ジョブはありません/);
    fireEvent.change(field(/サーバー上のパス/), { target: { value: "docs/a.docx" } });
    fireEvent.change(field("対象テナントID（必須）"), { target: { value: "tenant_a" } });
    fireEvent.change(field("ステージングコレクション（必須・非本番のみ）"), { target: { value: "prod" } });
    fireEvent.click(screen.getByRole("button", { name: "検証／取込を実行" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("refusing production/default collection");
  });
});

describe("the ingestion page: promote and delete", () => {
  it("checks the promotion without a change, and confirms before the real one", async () => {
    const mock = routes({ "GET /admin/ingestion/jobs": { jobs: [] }, "POST /admin/ingestion/promote": { collection: "staging_v1", documents: 2, chunks: 10, execute: false } });
    render(<IngestionPage />);
    await screen.findByText(/ジョブはありません/);
    fireEvent.click(screen.getByRole("button", { name: "反映内容を確認" }));
    expect(screen.getByRole("alert")).toHaveTextContent("テナントIDとステージングコレクションを入力してください");
    const [tenant, collection] = [screen.getAllByLabelText("テナントID")[0], field("ステージングコレクション")];
    fireEvent.change(tenant, { target: { value: "tenant_a" } });
    fireEvent.change(collection, { target: { value: "staging_v1" } });
    fireEvent.click(screen.getByRole("button", { name: "反映内容を確認" }));
    await screen.findByText(/文書数/);
    expect(call(mock, "POST /admin/ingestion/promote")!.body).toEqual({ tenant_id: "tenant_a", collection: "staging_v1", execute: false });

    fireEvent.click(screen.getByRole("button", { name: "本番へ反映する" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("staging_v1 の文書（tenant_a）を本番へ反映します。よろしいですか？");
    fireEvent.click(within(dialog).getByRole("button", { name: "実行" }));
    await waitFor(() => expect(mock.mock.calls.filter(([u]) => u === "/admin/ingestion/promote")).toHaveLength(2));
    const last = JSON.parse((mock.mock.calls.filter(([u]) => u === "/admin/ingestion/promote")[1][1] as RequestInit).body as string);
    expect(last.execute).toBe(true);
  });

  it("tells an administrator who may not touch the tenant that it is not allowed", async () => {
    routes({ "GET /admin/ingestion/jobs": { jobs: [] }, "POST /admin/ingestion/promote": jsonResponse({ detail: "scope" }, 403) });
    render(<IngestionPage />);
    await screen.findByText(/ジョブはありません/);
    fireEvent.change(screen.getAllByLabelText("テナントID")[0], { target: { value: "tenant_b" } });
    fireEvent.change(field("ステージングコレクション"), { target: { value: "staging_v1" } });
    fireEvent.click(screen.getByRole("button", { name: "反映内容を確認" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("この管理者には、このテナントの操作が許可されていません。");
  });

  it("lists the documents to delete, and asks again with the count before deleting", async () => {
    const mock = routes({ "GET /admin/ingestion/jobs": { jobs: [] }, "POST /admin/ingestion/documents/delete": { collection: "served", chunks: 3, execute: false } });
    render(<IngestionPage />);
    await screen.findByText(/ジョブはありません/);
    fireEvent.click(screen.getByRole("button", { name: "削除対象を確認" }));
    expect(screen.getByRole("alert")).toHaveTextContent("テナントIDを入力してください");
    fireEvent.change(screen.getAllByLabelText("テナントID")[1], { target: { value: "tenant_a" } });
    fireEvent.change(field(/文書名（1行に1つ/), { target: { value: "経費規程.pdf\n\n出張規程.pdf" } });
    fireEvent.click(screen.getByRole("button", { name: "削除対象を確認" }));
    await screen.findByText(/チャンク数/);
    expect(call(mock, "POST /admin/ingestion/documents/delete")!.body).toEqual({
      tenant_id: "tenant_a",
      source_docs: ["経費規程.pdf", "出張規程.pdf"],
      collection: null,
      execute: false,
    });
    fireEvent.click(screen.getByRole("button", { name: "削除する" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("2 件の文書を本番から削除します。元に戻せません。よろしいですか？");
    // the dialog's cancel button has the focus: a stray Enter does not delete
    expect(within(dialog).getByRole("button", { name: "キャンセル" })).toHaveFocus();
    fireEvent.click(within(dialog).getByRole("button", { name: "キャンセル" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mock.mock.calls.filter(([u]) => u === "/admin/ingestion/documents/delete")).toHaveLength(1);
  });
});
