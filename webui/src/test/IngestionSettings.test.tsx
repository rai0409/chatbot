import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { IngestionPage } from "../pages/Ingestion";
import { jsonResponse } from "./fixtures";
import { JOBS, SETTINGS } from "./ingestionFixtures";

afterEach(cleanup);

type Handler = (init: RequestInit) => Response;

function routes(table: Record<string, Handler | unknown>) {
  const all: Record<string, Handler | unknown> = { "GET /admin/ingestion/jobs": { jobs: JOBS }, "GET /admin/ingestion/settings": SETTINGS, ...table };
  const mock = vi.fn((url: string, init: RequestInit = {}) => {
    const key = `${(init.method ?? "GET").toUpperCase()} ${url.split("?")[0]}`;
    const entry = all[key];
    if (typeof entry === "function") return Promise.resolve((entry as Handler)(init));
    return Promise.resolve(entry === undefined ? jsonResponse({ detail: "no route" }, 404) : jsonResponse(entry));
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}

function posted(mock: ReturnType<typeof vi.fn>) {
  const found = mock.mock.calls.filter(([url, init]) => String(url) === "/admin/ingestion/settings" && (init as RequestInit)?.method === "POST");
  return found.map(([, init]) => JSON.parse(String((init as RequestInit).body)));
}

beforeEach(() => {
  document.cookie = "admin_csrf=token-123";
  window.sessionStorage.clear();
  vi.stubGlobal("location", { ...window.location, assign: vi.fn(), pathname: "/console/ingestion" });
});

async function openSettings() {
  return screen.findByRole("heading", { name: "取込の設定" });
}

describe("the ingestion settings", () => {
  it("lists the settings by group with their value, source and default", async () => {
    routes({});
    render(<IngestionPage />);
    await openSettings();
    expect(await screen.findByRole("heading", { name: "PDF・OCR" })).toBeInTheDocument();
    const row = screen.getByRole("row", { name: /変換プロセスのメモリ上限.*画面で変更/ });
    expect(row).toHaveTextContent("4,096 MB");
    expect(row).toHaveTextContent("画面で変更");
    expect(row).toHaveTextContent("変更者: 山田太郎（理由: メモリの上限を設ける）");
    expect(screen.getByRole("row", { name: /PDF の表の抽出/ })).toHaveTextContent("環境設定");
    expect(screen.getByRole("row", { name: /PDF の表の抽出/ })).toHaveTextContent("有効");
    expect(screen.getByRole("heading", { name: "最近の変更" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "— → 4096" })).toBeInTheDocument();
  });

  it("changes a number after validating it, with a reason and a name", async () => {
    const mock = routes({ "POST /admin/ingestion/settings": { ok: true, rev: 4 } });
    render(<IngestionPage />);
    fireEvent.click(await screen.findByRole("button", { name: "変更: 同時アップロード数" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("同時アップロード数"), { target: { value: "9999" } });
    fireEvent.change(within(dialog).getByLabelText("理由（必須）"), { target: { value: "同時利用が増えた" } });
    fireEvent.change(within(dialog).getByLabelText("あなたの名前（必須）"), { target: { value: "山田太郎" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "保存" }));
    expect(await within(dialog).findByText("0 から 256 の範囲で入力してください。")).toBeInTheDocument();
    expect(posted(mock)).toHaveLength(0);
    fireEvent.change(within(dialog).getByLabelText("同時アップロード数"), { target: { value: "8" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "保存" }));
    expect(await screen.findByText("保存しました。")).toBeInTheDocument();
    expect(posted(mock)).toEqual([{ name: "INGEST_UPLOAD_MAX_CONCURRENT", value: 8, reason: "同時利用が増えた", actor: "山田太郎", expected_rev: 3 }]);
  });

  it("will not send without a reason and a name", async () => {
    const mock = routes({});
    render(<IngestionPage />);
    fireEvent.click(await screen.findByRole("button", { name: "変更: 同時アップロード数" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "保存" }));
    expect(await within(screen.getByRole("dialog")).findByText("理由と名前を入力してください。")).toBeInTheDocument();
    expect(posted(mock)).toHaveLength(0);
  });

  it("changes a switch and a choice", async () => {
    const mock = routes({ "POST /admin/ingestion/settings": { ok: true, rev: 4 } });
    render(<IngestionPage />);
    fireEvent.click(await screen.findByRole("button", { name: "変更: PDF の表の抽出" }));
    let dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("PDF の表の抽出"), { target: { value: "false" } });
    fireEvent.change(within(dialog).getByLabelText("理由（必須）"), { target: { value: "表の誤検出が多い" } });
    fireEvent.change(within(dialog).getByLabelText("あなたの名前（必須）"), { target: { value: "山田太郎" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "保存" }));
    await screen.findByText("保存しました。");
    fireEvent.click(screen.getByRole("button", { name: "変更: OCR の使用" }));
    dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("OCR の使用"), { target: { value: "false" } });
    fireEvent.change(within(dialog).getByLabelText("理由（必須）"), { target: { value: "OCR は使わない" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "保存" }));
    await waitFor(() => expect(posted(mock)).toHaveLength(2));
    expect(posted(mock)[0]).toMatchObject({ name: "PDF_TABLES", value: false, actor: "山田太郎" });
    expect(posted(mock)[1]).toMatchObject({ name: "INGEST_PDF_OCR", value: "false", actor: "山田太郎" }); // the name is remembered
  });

  it("resets an override with a reason", async () => {
    const mock = routes({ "POST /admin/ingestion/settings": { ok: true, rev: 4 } });
    render(<IngestionPage />);
    fireEvent.click(await screen.findByRole("button", { name: "元に戻す: 変換プロセスのメモリ上限" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("変換ワーカーを再起動します");
    fireEvent.change(within(dialog).getByLabelText("理由（必須）"), { target: { value: "上限を外す" } });
    fireEvent.change(within(dialog).getByLabelText("あなたの名前（必須）"), { target: { value: "山田太郎" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "元に戻す" }));
    expect(await screen.findByText("元に戻しました。")).toBeInTheDocument();
    expect(posted(mock)[0]).toEqual({ name: "INGEST_CONVERT_MAX_MEMORY_MB", reset: true, reason: "上限を外す", actor: "山田太郎", expected_rev: 3 });
    expect(screen.queryByRole("button", { name: "元に戻す: PDF の表の抽出" })).toBeNull(); // only an override can be reset
  });

  it("says so when someone else changed the settings meanwhile, and reloads", async () => {
    const mock = routes({ "POST /admin/ingestion/settings": () => jsonResponse({ detail: "changed" }, 409) });
    render(<IngestionPage />);
    fireEvent.click(await screen.findByRole("button", { name: "変更: 同時アップロード数" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("理由（必須）"), { target: { value: "試験" } });
    fireEvent.change(within(dialog).getByLabelText("あなたの名前（必須）"), { target: { value: "山田太郎" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "保存" }));
    expect(await screen.findByText(/他の人が先に設定を変更しました/)).toBeInTheDocument();
    await waitFor(() => expect(mock.mock.calls.filter(([u]) => String(u) === "/admin/ingestion/settings" && !(mock.mock.calls[0][1] as RequestInit)?.method).length).toBeGreaterThan(1));
  });

  it("shows the server's reason when a value is refused", async () => {
    routes({ "POST /admin/ingestion/settings": () => jsonResponse({ detail: "JA_CHUNK_TARGET_POLICY_MAX_CHARS: the minimum (400) must be smaller than the maximum (300)" }, 400) });
    render(<IngestionPage />);
    fireEvent.click(await screen.findByRole("button", { name: "変更: 規程・仕様の最大文字数" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("規程・仕様の最大文字数"), { target: { value: "300" } });
    fireEvent.change(within(dialog).getByLabelText("理由（必須）"), { target: { value: "試験" } });
    fireEvent.change(within(dialog).getByLabelText("あなたの名前（必須）"), { target: { value: "山田太郎" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "保存" }));
    expect(await screen.findByText(/保存できませんでした: JA_CHUNK_TARGET_POLICY_MAX_CHARS: the minimum \(400\)/)).toBeInTheDocument();
  });

  it("is read-only for an administrator who cannot change it", async () => {
    routes({ "GET /admin/ingestion/settings": { ...SETTINGS, can_edit: false } });
    render(<IngestionPage />);
    expect(await screen.findByText(/インストール全体の管理者だけが変更できます/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^変更: / })).toBeNull();
    expect(screen.getByRole("row", { name: /同時アップロード数/ })).toBeInTheDocument();
  });

  it("shows an error with a retry when the settings cannot be loaded", async () => {
    routes({ "GET /admin/ingestion/settings": () => jsonResponse({ detail: "x" }, 500) });
    render(<IngestionPage />);
    expect(await screen.findByText("取込の設定を取得できませんでした。")).toBeInTheDocument();
  });
});

describe("the options of one ingestion", () => {
  it("sends the document type and the OCR choice only when they are chosen", async () => {
    const mock = routes({ "POST /admin/ingestion/raw-documents": { status: "ok", ok: true } });
    render(<IngestionPage />);
    await openSettings();
    fireEvent.change(screen.getByLabelText("対象テナントID（必須）"), { target: { value: "tenant_a" } });
    fireEvent.change(screen.getByLabelText("ステージングコレクション（必須・非本番のみ）"), { target: { value: "staging_v1" } });
    fireEvent.change(screen.getByLabelText(/サーバー上のパス/), { target: { value: "docs/a.pdf" } });
    fireEvent.click(screen.getByRole("button", { name: "検証／取込を実行" }));
    await screen.findByRole("region", { name: "検証／取込の結果" });
    const first = JSON.parse(String(mock.mock.calls.find(([u]) => String(u) === "/admin/ingestion/raw-documents")![1]!.body));
    expect(first).not.toHaveProperty("doc_type");
    expect(first).not.toHaveProperty("ocr");

    fireEvent.change(screen.getByLabelText("文書の種別（PDF のみ）"), { target: { value: "faq" } });
    fireEvent.change(screen.getByLabelText("OCR（PDF のみ・文字のないページ）"), { target: { value: "false" } });
    fireEvent.click(screen.getByRole("button", { name: "検証／取込を実行" }));
    await waitFor(() => expect(mock.mock.calls.filter(([u]) => String(u) === "/admin/ingestion/raw-documents")).toHaveLength(2));
    const second = JSON.parse(String(mock.mock.calls.filter(([u]) => String(u) === "/admin/ingestion/raw-documents")[1][1]!.body));
    expect(second).toMatchObject({ doc_type: "faq", ocr: false });
  });
});
