import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ExtractionPage } from "../pages/Extraction";
import { PAGE, callsTo, defaultRoutes, routeFetch } from "./extractionFixtures";
import { jsonResponse } from "./fixtures";

const createUrl = vi.fn(() => "blob:page-1");
const revokeUrl = vi.fn();

beforeEach(() => {
  document.cookie = "admin_csrf=token-123";
  vi.stubGlobal("location", { ...window.location, assign: vi.fn(), pathname: "/console/extraction" });
  URL.createObjectURL = createUrl;
  URL.revokeObjectURL = revokeUrl;
  createUrl.mockClear();
  revokeUrl.mockClear();
});
afterEach(cleanup);

async function openFirstLine() {
  fireEvent.click(await screen.findByRole("button", { name: "請求書番号 12O45", hidden: false }));
  return screen.findByRole("textbox", { name: "補正後の文字" });
}

describe("the extraction review page", () => {
  it("lists documents and lines with their state, and shows the page image with boxes", async () => {
    routeFetch(defaultRoutes());
    render(<ExtractionPage />);
    expect(await screen.findByRole("row", { name: /scan-a\.pdf 2 10 2 1 0/ })).toBeInTheDocument();
    const row = (await screen.findAllByRole("row", { name: /請求書番号 12O45/ }))[0];
    expect(row).toHaveTextContent("なし");
    expect(row).toHaveTextContent("未確認");
    expect(screen.getByRole("row", { name: /株式会社サンプル/ })).toHaveTextContent("補正済み");

    await openFirstLine();
    const img = await screen.findByRole("img", { name: "ページ 1" });
    expect(img).toHaveAttribute("src", "blob:page-1");
    expect(screen.getByTestId("box-l1")).toHaveClass("weak", "sel");
    expect(screen.getByTestId("box-l1").style.left).toBe("10%");
    expect(screen.getByTestId("box-l2")).toHaveClass("weak");
    expect(screen.queryByTestId("box-l3")).toBeNull(); // no position, no box
    expect(screen.getByText("OCR の文字: 請求書番号 12O45")).toBeInTheDocument();
  });

  it("revokes the object URL when the page goes away", async () => {
    routeFetch(defaultRoutes());
    const { unmount } = render(<ExtractionPage />);
    await openFirstLine();
    await screen.findByRole("img");
    unmount();
    expect(revokeUrl).toHaveBeenCalledWith("blob:page-1");
  });

  it("says the image is unavailable when the file is gone, and still allows the correction", async () => {
    routeFetch(defaultRoutes({ "GET /admin/extraction/page": () => jsonResponse({ ...PAGE, preview_available: false }) }));
    render(<ExtractionPage />);
    await openFirstLine();
    expect(await screen.findByText(/ページ画像は表示できません/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "補正を保存" })).toBeInTheDocument();
  });

  it("shows an error with a retry when the lines cannot be loaded, and an empty hint", async () => {
    let fail = true;
    routeFetch(
      defaultRoutes({
        "GET /admin/extraction/items": () => (fail ? jsonResponse({ detail: "x" }, 500) : jsonResponse({ items: [] })),
      }),
    );
    render(<ExtractionPage />);
    const alert = await screen.findByText("見るべき行を取得できませんでした");
    fail = false;
    fireEvent.click(within(alert.closest("[role=alert]") as HTMLElement).getByRole("button", { name: "再読み込み" }));
    expect(await screen.findByText(/対象の行はありません/)).toBeInTheDocument();
  });

  it("saves a correction with Enter, sends the CSRF token and tenant, and moves the focus back to the list", async () => {
    const mock = routeFetch(defaultRoutes({ "POST /admin/extraction/correct": () => jsonResponse({ id: "a" }) }));
    render(<ExtractionPage />);
    const box = await openFirstLine();
    expect(box).toHaveFocus();
    fireEvent.change(box, { target: { value: "請求書番号 12045" } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(await screen.findByText("保存しました")).toBeInTheDocument();
    const [url, init] = callsTo(mock, "POST /admin/extraction/correct")[0];
    expect(url).toBe("/admin/extraction/correct");
    expect(JSON.parse(init!.body as string)).toEqual({ tenant_id: "default", source_doc: "scan-a.pdf", page: 1, line_id: "l1", text: "請求書番号 12045" });
    expect(new Headers(init!.headers).get("X-CSRF-Token")).toBe("token-123");
    await waitFor(() => expect(screen.queryByRole("textbox", { name: "補正後の文字" })).toBeNull());
    await waitFor(() => expect(document.activeElement).toHaveAttribute("data-pick"));
  });

  it("does not save an unchanged or empty text and keeps Shift+Enter for a line break", async () => {
    const mock = routeFetch(defaultRoutes({ "POST /admin/extraction/correct": () => jsonResponse({}) }));
    render(<ExtractionPage />);
    const box = await openFirstLine();
    fireEvent.keyDown(box, { key: "Enter" });
    expect(await screen.findByRole("alert")).toHaveTextContent("文字が変わっていません");
    fireEvent.change(box, { target: { value: "  " } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(await screen.findByRole("alert")).toHaveTextContent("補正後の文字を入力してください");
    fireEvent.keyDown(box, { key: "Enter", shiftKey: true });
    expect(callsTo(mock, "POST /admin/extraction/correct")).toHaveLength(0);
  });

  it("closes the editor with Escape and gives the focus back to the row that opened it", async () => {
    routeFetch(defaultRoutes());
    render(<ExtractionPage />);
    const opener = await screen.findByRole("button", { name: "請求書番号 12O45" });
    fireEvent.click(opener);
    const box = await screen.findByRole("textbox", { name: "補正後の文字" });
    fireEvent.keyDown(box, { key: "Escape" });
    expect(screen.queryByRole("textbox", { name: "補正後の文字" })).toBeNull();
    expect(opener).toHaveFocus();
  });

  it("confirms a line as it is", async () => {
    const mock = routeFetch(defaultRoutes({ "POST /admin/extraction/confirm": () => jsonResponse({}) }));
    render(<ExtractionPage />);
    await openFirstLine();
    fireEvent.click(screen.getByRole("button", { name: "このまま正しい" }));
    expect(await screen.findByText("確認済みにしました")).toBeInTheDocument();
    expect(JSON.parse(callsTo(mock, "POST /admin/extraction/confirm")[0][1]!.body as string)).toMatchObject({ line_id: "l1", page: 1 });
  });

  it("asks before reverting a decision, and only then calls the server", async () => {
    const mock = routeFetch(defaultRoutes({ "POST /admin/extraction/revert": () => jsonResponse({ reverted: true }) }));
    render(<ExtractionPage />);
    const page = await screen.findByRole("button", { name: "株式会社サンプル" });
    fireEvent.click(page);
    const revert = await screen.findByRole("button", { name: "判断を取り消す" });
    expect(revert).toBeEnabled();
    fireEvent.click(revert);
    const dialog = screen.getByRole("dialog");
    expect(callsTo(mock, "POST /admin/extraction/revert")).toHaveLength(0);
    fireEvent.click(within(dialog).getByRole("button", { name: "取り消す" }));
    expect(await screen.findByText("判断を取り消しました")).toBeInTheDocument();
    expect(callsTo(mock, "POST /admin/extraction/revert")).toHaveLength(1);
  });

  it("explains a conflict and a missing permission", async () => {
    let status = 409;
    routeFetch(defaultRoutes({ "POST /admin/extraction/confirm": () => jsonResponse({ detail: "x" }, status) }));
    render(<ExtractionPage />);
    await openFirstLine();
    fireEvent.click(screen.getByRole("button", { name: "このまま正しい" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("他の人が先にこの行を変更した");
    status = 403;
    fireEvent.click(screen.getByRole("button", { name: "このまま正しい" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("権限がありません"));
  });

  it("books a re-ingest only after a confirmation that says what it does", async () => {
    const mock = routeFetch(defaultRoutes({ "POST /admin/extraction/reingest": () => jsonResponse({ sources: ["src-1"], note: "n" }) }));
    render(<ExtractionPage />);
    fireEvent.click(await screen.findByRole("button", { name: /この文書の再取り込みを予約: scan-a\.pdf/ }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("次の反映で取り込み直す");
    expect(within(dialog).getByRole("button", { name: "キャンセル" })).toHaveFocus();
    fireEvent.click(within(dialog).getByRole("button", { name: "予約する" }));
    expect(await screen.findByText(/予約しました。次の反映で取り込み直されます（ソース: src-1）/)).toBeInTheDocument();
    const [, init] = callsTo(mock, "POST /admin/extraction/reingest")[0];
    expect(JSON.parse(init!.body as string)).toEqual({ tenant_id: "default", source_doc: "scan-a.pdf" });
    expect(new Headers(init!.headers).get("X-CSRF-Token")).toBe("token-123");
  });

  it("says to upload again when no registered source holds the document", async () => {
    routeFetch(defaultRoutes({ "POST /admin/extraction/reingest": () => jsonResponse({ sources: [], note: "n" }) }));
    render(<ExtractionPage />);
    fireEvent.click(await screen.findByRole("button", { name: /この文書の再取り込みを予約: scan-a\.pdf/ }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "予約する" }));
    expect(await screen.findByText(/登録済みのソースがありません/)).toBeInTheDocument();
  });

  it("sends the filters, rejects a confidence outside 0 to 100, and filters by document", async () => {
    const mock = routeFetch(defaultRoutes());
    render(<ExtractionPage />);
    await screen.findByRole("row", { name: /scan-a\.pdf 2 10/ });
    fireEvent.change(screen.getByLabelText("この信頼度未満"), { target: { value: "150" } });
    fireEvent.click(screen.getByRole("button", { name: "読み込み" }));
    expect(screen.getByRole("alert")).toHaveTextContent("0 から 100");
    fireEvent.change(screen.getByLabelText("この信頼度未満"), { target: { value: "70" } });
    fireEvent.click(screen.getByLabelText(/判断済みも表示/));
    fireEvent.change(screen.getByLabelText("テナント"), { target: { value: "tenant-b" } });
    fireEvent.click(screen.getByRole("button", { name: "読み込み" }));
    await waitFor(() => {
      const last = callsTo(mock, "GET /admin/extraction/items").at(-1)![0] as string;
      expect(last).toContain("tenant_id=tenant-b");
      expect(last).toContain("max_confidence=70");
      expect(last).toContain("include_reviewed=true");
    });
    fireEvent.click(await screen.findByRole("button", { name: /この文書の行を見る: scan-a\.pdf/ }));
    await waitFor(() => expect((callsTo(mock, "GET /admin/extraction/items").at(-1)![0] as string)).toContain("source_doc=scan-a.pdf"));
    expect(screen.getByText("文書で絞り込み中: scan-a.pdf")).toBeInTheDocument();
  });

  it("goes to the login page when the session has ended", async () => {
    routeFetch(defaultRoutes({ "GET /admin/extraction/docs": () => jsonResponse({ detail: "x" }, 401), "GET /admin/extraction/items": () => jsonResponse({ detail: "x" }, 401) }));
    render(<ExtractionPage />);
    await waitFor(() => expect(window.location.assign).toHaveBeenCalledWith("/admin/login?next=%2Fconsole%2Fextraction"));
  });
});
