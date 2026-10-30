import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RecordsPage } from "../pages/Records";
import { jsonResponse } from "./fixtures";
import { PAGE, REC_ANON, REC_REVIEWED, REC_TEXT, router, sent } from "./recordsFixtures";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

beforeEach(() => {
  document.cookie = "admin_csrf=token-123";
  vi.stubGlobal("location", { ...window.location, assign: vi.fn(), pathname: "/console/records" });
});

function show() {
  return render(
    <MemoryRouter>
      <RecordsPage />
    </MemoryRouter>,
  );
}

describe("the records list", () => {
  it("shows the rows with time, tenant, question, mode, degraded marker, feedback and judgement", async () => {
    const fetchMock = vi.fn(router({ "GET /admin/records": () => jsonResponse(PAGE) }));
    vi.stubGlobal("fetch", fetchMock);
    show();
    expect(screen.getByRole("status")).toHaveTextContent("読み込み中");
    const row = await screen.findByRole("row", { name: /有給休暇は何日/ });
    expect(within(row).getByText("文書から回答")).toBeInTheDocument();
    expect(within(row).getByText("縮退")).toBeInTheDocument();
    expect(within(row).getByText("悪い")).toBeInTheDocument();
    expect(within(row).getByText("未レビュー")).toBeInTheDocument();
    const anon = screen.getByRole("row", { name: /sample-tenant/ });
    expect(within(anon).getByText("（本文なし）")).toBeInTheDocument();
    expect(screen.getByText(/監査ログに残ります/)).toBeInTheDocument();
    expect(fetchMock.mock.calls[0][0]).toBe("/admin/records?days=7&limit=50&offset=0");
  });

  it("says what to do when nothing matches", async () => {
    vi.stubGlobal("fetch", vi.fn(router({ "GET /admin/records": () => jsonResponse({ total: 0, offset: 0, records: [] }) })));
    show();
    expect(await screen.findByText(/条件に合う記録がありません/)).toBeInTheDocument();
  });

  it("shows an error with a retry button", async () => {
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(router({ "GET /admin/records": () => (calls++ === 0 ? jsonResponse({ detail: "boom" }, 500) : jsonResponse(PAGE)) })),
    );
    show();
    expect(await screen.findByRole("alert")).toHaveTextContent("読み込めませんでした");
    fireEvent.click(screen.getByRole("button", { name: "再読み込み" }));
    expect(await screen.findByRole("row", { name: /有給休暇は何日/ })).toBeInTheDocument();
  });

  it("sends the filters and pages by 50", async () => {
    const fetchMock = vi.fn(router({ "GET /admin/records": () => jsonResponse({ ...PAGE, total: 120 }) }));
    vi.stubGlobal("fetch", fetchMock);
    show();
    await screen.findByRole("row", { name: /有給休暇は何日/ });
    fireEvent.change(screen.getByLabelText("期間"), { target: { value: "90" } });
    fireEvent.change(screen.getByLabelText("レビュー"), { target: { value: "no" } });
    fireEvent.change(screen.getByLabelText("利用者の評価"), { target: { value: "bad" } });
    fireEvent.change(screen.getByLabelText("質問・回答の文字"), { target: { value: "有給" } });
    fireEvent.click(screen.getByLabelText("回答を控えたものだけ"));
    fireEvent.click(screen.getByRole("button", { name: "絞り込む" }));
    await waitFor(() => expect(fetchMock.mock.calls.length).toBe(2));
    const query = new URLSearchParams(String(fetchMock.mock.calls[1][0]).split("?")[1]);
    expect(Object.fromEntries(query)).toEqual({ days: "90", reviewed: "false", feedback: "bad", text: "有給", abstained: "true", limit: "50", offset: "0" });
    await screen.findByRole("row", { name: /有給休暇は何日/ });
    expect(screen.getByText("120 件中 1～2 件目")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "前の 50 件" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "次の 50 件" }));
    await waitFor(() => expect(fetchMock.mock.calls.length).toBe(3));
    expect(String(fetchMock.mock.calls[2][0])).toContain("offset=50");
  });
});

describe("a record and its judgement", () => {
  function routes(extra: Record<string, (init: RequestInit | undefined) => Response> = {}) {
    return vi.fn(
      router({
        "GET /admin/records": () => jsonResponse(PAGE),
        [`GET /admin/records/${REC_TEXT.id}`]: () => jsonResponse(REC_TEXT),
        [`GET /admin/records/${REC_ANON.id}`]: () => jsonResponse(REC_ANON),
        ...extra,
      }),
    );
  }

  async function open(id: string) {
    fireEvent.click(await screen.findByRole("button", { name: `記録 ${id} を開く` }));
    return within(await screen.findByRole("complementary"));
  }

  it("shows the question, answer, sources and feedback of the selected record", async () => {
    vi.stubGlobal("fetch", routes());
    show();
    const detail = await open(REC_TEXT.id);
    expect(await detail.findByText("入社6か月後に10日付与されます。")).toBeInTheDocument();
    expect(detail.getByText(/就業規則.pdf（12 ページ）/)).toBeInTheDocument();
    expect(detail.getByText("悪い")).toBeInTheDocument();
    expect(detail.getByText("古い規則です")).toBeInTheDocument();
  });

  it("says so when a record has no text instead of an empty panel", async () => {
    vi.stubGlobal("fetch", routes());
    show();
    const detail = await open(REC_ANON.id);
    expect(await detail.findByRole("note")).toHaveTextContent("質問と回答の本文はありません");
    expect(detail.queryByRole("button", { name: "判定を保存" })).toBeNull();
  });

  it("says so when a record was anonymized by retention", async () => {
    vi.stubGlobal("fetch", routes({ [`GET /admin/records/${REC_ANON.id}`]: () => jsonResponse({ ...REC_ANON, anonymized: true }) }));
    show();
    const detail = await open(REC_ANON.id);
    expect(await detail.findByRole("note")).toHaveTextContent("匿名化されたため");
  });

  it("does not save a judgement other than correct without a reason or a note, or without a name", async () => {
    const fetchMock = routes();
    vi.stubGlobal("fetch", fetchMock);
    show();
    const detail = await open(REC_TEXT.id);
    await detail.findByRole("button", { name: "判定を保存" });
    fireEvent.click(detail.getByLabelText("誤り"));
    fireEvent.click(detail.getByRole("button", { name: "判定を保存" }));
    expect(detail.getByRole("alert")).toHaveTextContent("あなたの名前を入力してください");
    fireEvent.change(detail.getByLabelText("あなたの名前"), { target: { value: "田中太郎" } });
    fireEvent.click(detail.getByRole("button", { name: "判定を保存" }));
    expect(detail.getByRole("alert")).toHaveTextContent("理由を選ぶかメモを書いてください");
    expect(sent(fetchMock)).toHaveLength(0);
  });

  it("sends the judgement with the CSRF token, remembers the name and shows the saved judgement", async () => {
    let saved = false;
    const fetchMock = routes({
      [`GET /admin/records/${REC_TEXT.id}`]: () => jsonResponse(saved ? REC_REVIEWED : REC_TEXT),
      [`POST /admin/records/${REC_TEXT.id}/review`]: () => {
        saved = true;
        return jsonResponse(REC_REVIEWED.review);
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    show();
    const detail = await open(REC_TEXT.id);
    await detail.findByRole("button", { name: "判定を保存" });
    fireEvent.click(detail.getByLabelText("一部誤り"));
    fireEvent.click(detail.getByLabelText("情報が古い"));
    fireEvent.change(detail.getByLabelText("メモ"), { target: { value: " 古い " } });
    fireEvent.change(detail.getByLabelText("期待する回答"), { target: { value: "年に10日です" } });
    fireEvent.change(detail.getByLabelText("あなたの名前"), { target: { value: "田中太郎" } });
    fireEvent.click(detail.getByRole("button", { name: "判定を保存" }));
    expect(await detail.findByText("判定を保存しました")).toBeInTheDocument();
    const [call] = sent(fetchMock);
    expect(call.url).toBe(`/admin/records/${REC_TEXT.id}/review`);
    expect(call.body).toEqual({
      verdict: "partial",
      reviewer: "田中太郎",
      reasons: ["outdated"],
      note: "古い",
      expected_answer: "年に10日です",
    });
    expect(call.token).toBe("token-123");
    expect(window.localStorage.getItem("console.actorName")).toBe("田中太郎");
    await waitFor(() => expect(detail.getByText(/現在の判定: 一部誤り/)).toBeInTheDocument());
  });

  it("tells the administrator when the judgement is not allowed (403) or refused (400)", async () => {
    const status = { code: 403 };
    vi.stubGlobal(
      "fetch",
      routes({ [`POST /admin/records/${REC_TEXT.id}/review`]: () => jsonResponse({ detail: "a verdict other than 'correct' needs a reason" }, status.code) }),
    );
    show();
    const detail = await open(REC_TEXT.id);
    await detail.findByRole("button", { name: "判定を保存" });
    fireEvent.click(detail.getByLabelText("正しい"));
    fireEvent.change(detail.getByLabelText("あなたの名前"), { target: { value: "田中太郎" } });
    fireEvent.click(detail.getByRole("button", { name: "判定を保存" }));
    expect(await detail.findByRole("alert")).toHaveTextContent("権限がありません");
    status.code = 400;
    fireEvent.click(detail.getByRole("button", { name: "判定を保存" }));
    await waitFor(() => expect(detail.getByRole("alert")).toHaveTextContent("判定を保存できませんでした: a verdict other than"));
  });

  it("makes a golden question from a judged record and links to the golden screen", async () => {
    const fetchMock = routes({
      [`GET /admin/records/${REC_TEXT.id}`]: () => jsonResponse(REC_REVIEWED),
      "POST /admin/golden/default/from-record": () => jsonResponse({ case_id: "gq-new1", question: "q" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    show();
    const detail = await open(REC_TEXT.id);
    fireEvent.click(await detail.findByRole("button", { name: "ゴールデン QA を作る" }));
    expect(await detail.findByText(/ID: gq-new1/)).toBeInTheDocument();
    expect(detail.getByRole("link", { name: "ゴールデン QA の画面を開く" })).toHaveAttribute("href", "/golden");
    const [call] = sent(fetchMock);
    expect(call.url).toBe("/admin/golden/default/from-record");
    expect(call.body).toEqual({ record_id: REC_TEXT.id });
    expect(call.token).toBe("token-123");
  });

  it("does not offer the golden question before the record is judged", async () => {
    vi.stubGlobal("fetch", routes());
    show();
    const detail = await open(REC_TEXT.id);
    expect(await detail.findByRole("button", { name: "ゴールデン QA を作る" })).toBeDisabled();
  });

  it("says when the record is gone (404)", async () => {
    vi.stubGlobal("fetch", routes({ [`GET /admin/records/${REC_TEXT.id}`]: () => jsonResponse({ detail: "record not found" }, 404) }));
    show();
    const detail = await open(REC_TEXT.id);
    expect(await detail.findByRole("alert")).toHaveTextContent("見つかりません");
  });
});
