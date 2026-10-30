import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChangesPage } from "../pages/Changes";
import { EVALUATIONS, KNOBS, LIST, MONITORING, VERSIONS, posts, proposal, stubApi } from "./changesFixtures";
import { jsonResponse } from "./fixtures";

const ACTOR_LABEL = "あなたの名前（記録に残ります）";

afterEach(cleanup);

beforeEach(() => {
  document.cookie = "admin_csrf=token-123";
  vi.stubGlobal("location", { ...window.location, assign: vi.fn(), pathname: "/console/changes" });
  try {
    window.localStorage.clear();
  } catch {
    /* ignore */
  }
});

function openTab(name: string) {
  fireEvent.click(screen.getByRole("tab", { name }));
}

async function openProposal(title: RegExp) {
  fireEvent.click(await screen.findByRole("button", { name: title }));
}

describe("proposals: the list", () => {
  it("shows the proposals with state, category, approvals and decision", async () => {
    stubApi({ "GET /admin/proposals": LIST });
    render(<ChangesPage />);
    expect(screen.getByRole("status")).toHaveTextContent("読み込み中");
    const row = await screen.findByRole("row", { name: /距離のしきい値を見直す/ });
    expect(within(row).getByText("評価済み")).toBeInTheDocument();
    expect(within(row).getByText("ガードのしきい値")).toBeInTheDocument();
    expect(within(row).getByText("0 / 2")).toBeInTheDocument();
    expect(within(row).getByText("合格")).toBeInTheDocument();
    expect(screen.getByRole("row", { name: /シノニムを足す/ })).toHaveTextContent("未分類");
  });

  it("asks the server for one state when the filter is used", async () => {
    const mock = stubApi({ "GET /admin/proposals": LIST, "GET /admin/proposals?state=evaluated": { ...LIST, proposals: [LIST.proposals[0]] } });
    render(<ChangesPage />);
    await screen.findByRole("row", { name: /シノニムを足す/ });
    fireEvent.change(screen.getByLabelText("状態で絞り込む"), { target: { value: "evaluated" } });
    await waitFor(() => expect(screen.queryByRole("row", { name: /シノニムを足す/ })).toBeNull());
    expect(mock.mock.calls.map((c) => c[0])).toContain("/admin/proposals?state=evaluated");
  });

  it("says what to do when there is no proposal", async () => {
    stubApi({ "GET /admin/proposals": { states: [], proposals: [] } });
    render(<ChangesPage />);
    expect(await screen.findByText(/「提案を作る」から/)).toBeInTheDocument();
  });

  it("shows an error with a retry button", async () => {
    const mock = vi.fn().mockResolvedValueOnce(jsonResponse({ detail: "boom" }, 500)).mockImplementation(() => Promise.resolve(jsonResponse(LIST)));
    vi.stubGlobal("fetch", mock);
    render(<ChangesPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("取得できませんでした");
    fireEvent.click(screen.getByRole("button", { name: "再読み込み" }));
    expect(await screen.findByRole("row", { name: /シノニムを足す/ })).toBeInTheDocument();
  });
});

describe("proposals: creating", () => {
  it("checks the id, then posts the proposal with the CSRF token and remembers the name", async () => {
    const created = proposal({ id: "p-010", title: "要望の整理", state: "observed", change: null, evaluation: null, history: [] }, { id: "p-010", state: "observed" });
    const mock = stubApi({ "GET /admin/proposals": { states: [], proposals: [] }, "POST /admin/proposals": created, "GET /admin/proposals/p-010": created });
    render(<ChangesPage />);
    fireEvent.click(await screen.findByRole("button", { name: "提案を作る" }));
    const submit = screen.getByRole("button", { name: "作成する" });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/提案の ID/), { target: { value: "bad id!" } });
    expect(screen.getByRole("alert")).toHaveTextContent("ID は英数字");
    fireEvent.change(screen.getByLabelText(/提案の ID/), { target: { value: "p-010" } });
    fireEvent.change(screen.getByLabelText("プロファイル ID"), { target: { value: "sample" } });
    fireEvent.change(screen.getByLabelText("件名"), { target: { value: "  要望の整理  " } });
    fireEvent.change(screen.getByLabelText(/気づいたきっかけ/), { target: { value: "問い合わせが増えた" } });
    fireEvent.change(screen.getByLabelText(ACTOR_LABEL), { target: { value: "山田太郎" } });
    fireEvent.click(submit);
    expect(await screen.findByRole("heading", { name: "要望の整理" })).toBeInTheDocument();
    const [call] = posts(mock);
    expect(call.url).toBe("/admin/proposals");
    expect(call.body).toEqual({ id: "p-010", profile_id: "sample", title: "要望の整理", actor: "山田太郎", source: { note: "問い合わせが増えた" } });
    expect(call.csrf).toBe("token-123");
    expect(window.localStorage.getItem("console.actorName")).toBe("山田太郎");
  });
});

describe("proposals: the detail and the moves", () => {
  it("shows the change, the evaluation, the approvals and the history, and only the moves this state allows", async () => {
    stubApi({ "GET /admin/proposals": LIST, "GET /admin/proposals/p-001": proposal() });
    render(<ChangesPage />);
    await openProposal(/開く: 距離のしきい値/);
    expect(await screen.findByRole("heading", { name: "距離のしきい値を見直す" })).toBeInTheDocument();
    expect(screen.getByText("RAG_HARD_MAX_DIST")).toBeInTheDocument();
    expect(screen.getByText("#2")).toBeInTheDocument();
    expect(screen.getByText("abcdef012345")).toBeInTheDocument();
    expect(screen.getByText(/2 人の承認が必要/)).toBeInTheDocument();
    expect(screen.getByText("観測 → 分類済み")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "承認する" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "変更を提案する" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "却下する" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "次の段階へ進める" })).toBeNull();
    expect(screen.queryByRole("button", { name: "分類する" })).toBeNull();
  });

  it("says there is no move for a closed proposal", async () => {
    stubApi({ "GET /admin/proposals": LIST, "GET /admin/proposals/p-001": proposal({ state: "retired" }, { state: "retired" }) });
    render(<ChangesPage />);
    await openProposal(/開く: 距離のしきい値/);
    expect(await screen.findByText("この状態で行える操作はありません。")).toBeInTheDocument();
  });

  it("classifies with a category from the grouped list, and needs the name", async () => {
    const observed = proposal({ state: "observed", category: null, change: null, evaluation: null }, { state: "observed" });
    const mock = stubApi({ "GET /admin/proposals": LIST, "GET /admin/proposals/p-001": observed, "POST /admin/proposals/p-001/classify": observed });
    render(<ChangesPage />);
    await openProposal(/開く: 距離のしきい値/);
    fireEvent.click(await screen.findByRole("button", { name: "分類する" }));
    const form = screen.getByRole("form", { name: "分類する" });
    fireEvent.change(within(form).getByLabelText("カテゴリ"), { target: { value: "synonym" } });
    const submit = within(form).getByRole("button", { name: "実行する" });
    expect(submit).toBeDisabled(); // no name yet
    expect(within(form).getByRole("group", { name: "プロファイルの変更" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(ACTOR_LABEL), { target: { value: "田中太郎" } });
    fireEvent.click(submit);
    expect(await screen.findByText("実行しました。")).toBeInTheDocument();
    const [call] = posts(mock);
    expect(call.body).toEqual({ actor: "田中太郎", category: "synonym", note: "" });
    expect(call.csrf).toBe("token-123");
  });

  it("proposes knob values, checks each against its kind and limits, and marks the high-risk ones", async () => {
    const classified = proposal({ state: "classified", change: null, evaluation: null, proposed_by: null }, { state: "classified" });
    const mock = stubApi({
      "GET /admin/proposals": LIST,
      "GET /admin/proposals/p-001": classified,
      "GET /admin/knobs": { knobs: KNOBS },
      "GET /admin/profiles/sample/versions": VERSIONS,
      "POST /admin/proposals/p-001/propose": classified,
    });
    render(<ChangesPage />);
    await openProposal(/開く: 距離のしきい値/);
    fireEvent.click(await screen.findByRole("button", { name: "変更を提案する" }));
    const form = await screen.findByRole("form", { name: "変更を提案する" });
    await within(form).findByRole("button", { name: "設定値を追加" });
    fireEvent.change(screen.getByLabelText(ACTOR_LABEL), { target: { value: "山田太郎" } });
    const submit = within(form).getByRole("button", { name: "実行する" });
    expect(submit).toBeDisabled();
    fireEvent.click(within(form).getByRole("button", { name: "設定値を追加" }));
    const pick = within(form).getByLabelText("設定項目 1");
    expect(within(pick).getByRole("option", { name: "RAG_HARD_MAX_DIST (高リスク)" })).toBeInTheDocument();
    fireEvent.change(pick, { target: { value: "RAG_HARD_MAX_DIST" } });
    fireEvent.change(within(form).getByLabelText("値 1"), { target: { value: "2" } });
    expect(within(form).getByRole("alert")).toHaveTextContent("0.3 から 1.5 の範囲");
    expect(submit).toBeDisabled();
    fireEvent.change(within(form).getByLabelText("値 1"), { target: { value: "0.9" } });
    expect(within(form).queryByRole("alert")).toBeNull();
    fireEvent.change(within(form).getByLabelText("プロファイル版"), { target: { value: "cccc3333dddd4444" } });
    expect(submit).toBeEnabled();
    fireEvent.click(submit);
    await screen.findByText("実行しました。");
    const [call] = posts(mock);
    expect(call.body).toEqual({ actor: "山田太郎", note: "", change: { profile_version: "cccc3333dddd4444", knobs: { RAG_HARD_MAX_DIST: 0.9 } } });
  });

  it("refuses a fraction for a whole-number knob", async () => {
    const classified = proposal({ state: "classified", category: "retrieval_setting", change: null, evaluation: null }, { state: "classified" });
    stubApi({
      "GET /admin/proposals": LIST,
      "GET /admin/proposals/p-001": classified,
      "GET /admin/knobs": { knobs: KNOBS },
      "GET /admin/profiles/sample/versions": VERSIONS,
    });
    render(<ChangesPage />);
    await openProposal(/開く: 距離のしきい値/);
    fireEvent.click(await screen.findByRole("button", { name: "変更を提案する" }));
    const form = await screen.findByRole("form", { name: "変更を提案する" });
    fireEvent.click(await within(form).findByRole("button", { name: "設定値を追加" }));
    fireEvent.change(within(form).getByLabelText("設定項目 1"), { target: { value: "VECTOR_TOP_K" } });
    fireEvent.change(within(form).getByLabelText("値 1"), { target: { value: "2.5" } });
    expect(within(form).getByRole("alert")).toHaveTextContent("整数");
  });

  it("proposes a manual text for a manual category", async () => {
    const classified = proposal({ state: "classified", category: "ingest_gap", change: null, evaluation: null }, { state: "classified" });
    const mock = stubApi({ "GET /admin/proposals": LIST, "GET /admin/proposals/p-001": classified, "POST /admin/proposals/p-001/propose": classified });
    render(<ChangesPage />);
    await openProposal(/開く: 距離のしきい値/);
    fireEvent.click(await screen.findByRole("button", { name: "変更を提案する" }));
    fireEvent.change(screen.getByLabelText(ACTOR_LABEL), { target: { value: "山田太郎" } });
    fireEvent.change(await screen.findByLabelText("手作業の内容"), { target: { value: "規程の最新版を取り込む" } });
    fireEvent.click(screen.getByRole("button", { name: "実行する" }));
    await screen.findByText("実行しました。");
    expect(posts(mock)[0].body).toEqual({ actor: "山田太郎", note: "", change: { manual: "規程の最新版を取り込む" } });
  });

  it("attaches an evaluation chosen from the ledger entries of this proposal", async () => {
    const proposed = proposal({ state: "proposed", evaluation: null }, { state: "proposed" });
    const mock = stubApi({
      "GET /admin/proposals": LIST,
      "GET /admin/proposals/p-001": proposed,
      "GET /admin/evaluations?limit=200": EVALUATIONS,
      "POST /admin/proposals/p-001/attach-evaluation": proposed,
    });
    render(<ChangesPage />);
    await openProposal(/開く: 距離のしきい値/);
    fireEvent.click(await screen.findByRole("button", { name: "評価を紐づける" }));
    const pick = await screen.findByLabelText("評価の記録");
    expect(within(pick).getAllByRole("option")).toHaveLength(2); // the empty choice and entry 2 (entry 1 is of another proposal)
    fireEvent.change(pick, { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText(ACTOR_LABEL), { target: { value: "田中太郎" } });
    fireEvent.click(screen.getByRole("button", { name: "実行する" }));
    await screen.findByText("実行しました。");
    expect(posts(mock)[0].body).toEqual({ actor: "田中太郎", ledger_seq: 2 });
  });

  it("asks for a reason to approve an insufficient evaluation, and explains the two-person rule", async () => {
    const insufficient = proposal({ evaluation: { ledger_seq: 2, run_id: "r", decision: "insufficient", eval_set_sha256: "a", entry_sha256: "b" } });
    const mock = stubApi({ "GET /admin/proposals": LIST, "GET /admin/proposals/p-001": insufficient, "POST /admin/proposals/p-001/approve": insufficient });
    render(<ChangesPage />);
    await openProposal(/開く: 距離のしきい値/);
    fireEvent.click(await screen.findByRole("button", { name: "承認する" }));
    const form = screen.getByRole("form", { name: "承認する" });
    expect(within(form).getByText(/自分の提案を承認できません/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(ACTOR_LABEL), { target: { value: "田中太郎" } });
    const submit = within(form).getByRole("button", { name: "実行する" });
    expect(submit).toBeDisabled();
    fireEvent.change(within(form).getByLabelText(/承知して承認する理由/), { target: { value: "期間が短いため" } });
    fireEvent.change(within(form).getByLabelText("メモ（任意）"), { target: { value: "確認済み" } });
    fireEvent.click(submit);
    await screen.findByText("実行しました。");
    expect(posts(mock)[0].body).toEqual({ actor: "田中太郎", note: "確認済み", accept_insufficient_reason: "期間が短いため" });
  });

  it("does not offer the insufficient reason for a passing evaluation, and blocks a failed one", async () => {
    stubApi({ "GET /admin/proposals": LIST, "GET /admin/proposals/p-001": proposal() });
    const { unmount } = render(<ChangesPage />);
    await openProposal(/開く: 距離のしきい値/);
    fireEvent.click(await screen.findByRole("button", { name: "承認する" }));
    expect(screen.queryByLabelText(/承知して承認する理由/)).toBeNull();
    unmount();
    cleanup();
    const failed = proposal({ evaluation: { ledger_seq: 2, run_id: "r", decision: "fail", eval_set_sha256: "a", entry_sha256: "b" } });
    stubApi({ "GET /admin/proposals": LIST, "GET /admin/proposals/p-001": failed });
    render(<ChangesPage />);
    await openProposal(/開く: 距離のしきい値/);
    fireEvent.click(await screen.findByRole("button", { name: "承認する" }));
    fireEvent.change(screen.getByLabelText(ACTOR_LABEL), { target: { value: "田中太郎" } });
    expect(screen.getByText(/不合格のため承認できません/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "実行する" })).toBeDisabled();
  });

  it("advances with written evidence and shows the next state", async () => {
    const approved = proposal({ state: "approved" }, { state: "approved" });
    const mock = stubApi({ "GET /admin/proposals": LIST, "GET /admin/proposals/p-001": approved, "POST /admin/proposals/p-001/advance": approved });
    render(<ChangesPage />);
    await openProposal(/開く: 距離のしきい値/);
    fireEvent.click(await screen.findByRole("button", { name: "次の段階へ進める" }));
    expect(screen.getByText("次の状態: シャドー運用")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(ACTOR_LABEL), { target: { value: "佐藤次郎" } });
    fireEvent.change(screen.getByLabelText(/根拠（必須）/), { target: { value: "シャドー比較で差が出なかった" } });
    fireEvent.click(screen.getByRole("button", { name: "実行する" }));
    await screen.findByText("実行しました。");
    expect(posts(mock)[0].body).toEqual({ actor: "佐藤次郎", evidence: "シャドー比較で差が出なかった" });
  });

  it("rejects and retires with a reason", async () => {
    const live = proposal({ state: "live" }, { state: "live" });
    const mock = stubApi({ "GET /admin/proposals": LIST, "GET /admin/proposals/p-001": live, "POST /admin/proposals/p-001/retire": live });
    render(<ChangesPage />);
    await openProposal(/開く: 距離のしきい値/);
    expect(screen.queryByRole("button", { name: "却下する" })).toBeNull(); // a live change is retired, not rejected
    fireEvent.click(await screen.findByRole("button", { name: "完了にする" }));
    expect(screen.getByText("完了にすると、この提案は閉じられます。")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(ACTOR_LABEL), { target: { value: "佐藤次郎" } });
    fireEvent.change(screen.getByLabelText("理由（必須）"), { target: { value: "役目を終えた" } });
    fireEvent.click(screen.getByRole("button", { name: "実行する" }));
    await screen.findByText("実行しました。");
    expect(posts(mock)[0].body).toEqual({ actor: "佐藤次郎", reason: "役目を終えた" });
  });

  it("shows the server's message for a conflict", async () => {
    const approved = proposal({ state: "approved" }, { state: "approved" });
    stubApi({
      "GET /admin/proposals": LIST,
      "GET /admin/proposals/p-001": approved,
      "POST /admin/proposals/p-001/advance": () => jsonResponse({ detail: "proposal 'p-001' was changed by someone else; reload it" }, 409),
    });
    render(<ChangesPage />);
    await openProposal(/開く: 距離のしきい値/);
    fireEvent.click(await screen.findByRole("button", { name: "次の段階へ進める" }));
    fireEvent.change(screen.getByLabelText(ACTOR_LABEL), { target: { value: "佐藤次郎" } });
    fireEvent.change(screen.getByLabelText(/根拠（必須）/), { target: { value: "確認" } });
    fireEvent.click(screen.getByRole("button", { name: "実行する" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("他の人が先に変更しました");
    expect(alert).toHaveTextContent("changed by someone else");
    expect(screen.getByRole("button", { name: "実行する" })).toBeEnabled();
  });

  it("shows the server's message for a refused move (400)", async () => {
    const evaluated = proposal();
    stubApi({
      "GET /admin/proposals": LIST,
      "GET /admin/proposals/p-001": evaluated,
      "POST /admin/proposals/p-001/approve": () => jsonResponse({ detail: "the proposer cannot approve their own change" }, 400),
    });
    render(<ChangesPage />);
    await openProposal(/開く: 距離のしきい値/);
    fireEvent.click(await screen.findByRole("button", { name: "承認する" }));
    fireEvent.change(screen.getByLabelText(ACTOR_LABEL), { target: { value: "山田太郎" } });
    fireEvent.click(screen.getByRole("button", { name: "実行する" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("the proposer cannot approve their own change");
  });

  it("turns read-only when the administrator is not allowed (403), and the page stays readable", async () => {
    stubApi({
      "GET /admin/proposals": LIST,
      "GET /admin/proposals/p-001": proposal(),
      "POST /admin/proposals/p-001/reject": () => jsonResponse({ detail: "forbidden" }, 403),
    });
    render(<ChangesPage />);
    await openProposal(/開く: 距離のしきい値/);
    fireEvent.click(await screen.findByRole("button", { name: "却下する" }));
    fireEvent.change(screen.getByLabelText(ACTOR_LABEL), { target: { value: "田中太郎" } });
    fireEvent.change(screen.getByLabelText("理由（必須）"), { target: { value: "不要" } });
    fireEvent.click(screen.getByRole("button", { name: "実行する" }));
    await waitFor(() => expect(screen.getByRole("note")).toHaveTextContent("変更する権限がありません"));
    expect(screen.getByRole("button", { name: "承認する" })).toBeDisabled();
    expect(screen.getByRole("heading", { name: "距離のしきい値を見直す" })).toBeInTheDocument();
  });
});

describe("profile versions", () => {
  const routes = {
    "GET /admin/proposals": LIST,
    "GET /admin/profiles": { profiles: [{ profile_id: "sample", versions: 2, active: "aaaa1111bbbb2222" }] },
    "GET /admin/profiles/sample/versions": VERSIONS,
  };

  it("lists the profiles and the versions with the active one marked and the history of the pointer", async () => {
    stubApi(routes);
    render(<ChangesPage />);
    openTab("プロファイル版");
    const row = await screen.findByRole("row", { name: /sample/ });
    expect(row).toHaveTextContent("aaaa1111bbbb2222");
    fireEvent.click(within(row).getByRole("button", { name: /版を見る/ }));
    const active = await screen.findByRole("row", { name: /aaaa1111bbbb2222/ });
    expect(within(active).getByText("現在有効")).toBeInTheDocument();
    expect(screen.getByRole("row", { name: /cccc3333dddd4444/ })).toHaveTextContent("synonyms.json, rules.json");
    expect(screen.getByRole("row", { name: /0000111122223333/ })).toBeInTheDocument();
    expect(screen.getByText(/すべてのテナントに/)).toBeInTheDocument();
  });

  it("activates a version after a reason, with the name, and says it affects every tenant", async () => {
    const mock = stubApi({ ...routes, "POST /admin/profiles/sample/activate": { active: {}, previous: {} } });
    render(<ChangesPage />);
    openTab("プロファイル版");
    fireEvent.click(await screen.findByRole("button", { name: /版を見る/ }));
    const row = await screen.findByRole("row", { name: /cccc3333dddd4444/ });
    fireEvent.click(within(row).getByRole("button", { name: "この版を有効にする" }));
    expect(screen.queryByRole("dialog")).toBeNull(); // the name comes first
    expect(screen.getByRole("alert")).toHaveTextContent("名前を入力");
    fireEvent.change(screen.getByLabelText(ACTOR_LABEL), { target: { value: "山田太郎" } });
    fireEvent.click(within(row).getByRole("button", { name: "この版を有効にする" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("すべてのテナント");
    const confirm = within(dialog).getByRole("button", { name: "実行" });
    expect(confirm).toBeDisabled();
    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "ルールの追加" } });
    fireEvent.click(confirm);
    expect(await screen.findByText("変更しました。")).toBeInTheDocument();
    const [call] = posts(mock);
    expect(call.url).toBe("/admin/profiles/sample/activate");
    expect(call.body).toEqual({ version: "cccc3333dddd4444", actor: "山田太郎", reason: "ルールの追加" });
    expect(call.csrf).toBe("token-123");
  });

  it("rolls back after a reason", async () => {
    const mock = stubApi({ ...routes, "POST /admin/profiles/sample/rollback": { active: {} } });
    window.localStorage.setItem("console.actorName", "田中太郎");
    render(<ChangesPage />);
    openTab("プロファイル版");
    fireEvent.click(await screen.findByRole("button", { name: /版を見る/ }));
    fireEvent.click(await screen.findByRole("button", { name: "前の版に戻す" }));
    fireEvent.change(within(screen.getByRole("dialog")).getByRole("textbox"), { target: { value: "問題が出た" } });
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "実行" }));
    await screen.findByText("変更しました。");
    expect(posts(mock)[0].body).toEqual({ actor: "田中太郎", reason: "問題が出た" });
  });

  it("tells the administrator when they are not allowed to activate (403)", async () => {
    stubApi({ ...routes, "POST /admin/profiles/sample/activate": () => jsonResponse({ detail: "forbidden" }, 403 ) });
    window.localStorage.setItem("console.actorName", "田中太郎");
    render(<ChangesPage />);
    openTab("プロファイル版");
    fireEvent.click(await screen.findByRole("button", { name: /版を見る/ }));
    const row = await screen.findByRole("row", { name: /cccc3333dddd4444/ });
    fireEvent.click(within(row).getByRole("button", { name: "この版を有効にする" }));
    fireEvent.change(within(screen.getByRole("dialog")).getByRole("textbox"), { target: { value: "理由" } });
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "実行" }));
    await waitFor(() => expect(screen.getByRole("note")).toHaveTextContent("変更する権限がありません"));
    expect(within(row).getByRole("button", { name: "この版を有効にする" })).toBeDisabled();
  });

  it("compares two versions and shows each file's result in words", async () => {
    const mock = stubApi({
      ...routes,
      "GET /admin/profiles/sample/diff?left=aaaa1111bbbb2222&right=source": { left: "aaaa1111bbbb2222", right: "source", files: { "synonyms.json": "changed", "rules.json": "same" }, identical: false },
    });
    render(<ChangesPage />);
    openTab("プロファイル版");
    fireEvent.click(await screen.findByRole("button", { name: /版を見る/ }));
    await screen.findByRole("row", { name: /cccc3333dddd4444/ });
    const run = screen.getByRole("button", { name: "比べる" });
    expect(run).toBeDisabled();
    fireEvent.change(screen.getByLabelText("比較元"), { target: { value: "aaaa1111bbbb2222" } });
    fireEvent.change(screen.getByLabelText("比較先"), { target: { value: "source" } });
    fireEvent.click(run);
    expect(await screen.findByText("違いがあります。")).toBeInTheDocument();
    const result = within(screen.getByText("違いがあります。").closest(".panel") as HTMLElement);
    expect(result.getByRole("row", { name: /synonyms.json/ })).toHaveTextContent("変更あり");
    expect(result.getByRole("row", { name: /rules.json/ })).toHaveTextContent("同じ");
    expect(mock.mock.calls.map((c) => c[0])).toContain("/admin/profiles/sample/diff?left=aaaa1111bbbb2222&right=source");
  });

  it("says when there is no stored profile", async () => {
    stubApi({ "GET /admin/proposals": LIST, "GET /admin/profiles": { profiles: [] } });
    render(<ChangesPage />);
    openTab("プロファイル版");
    expect(await screen.findByText("保存済みのプロファイルはまだありません。")).toBeInTheDocument();
  });
});

describe("evaluation ledger, settings and monitoring", () => {
  const base = { "GET /admin/proposals": LIST };

  it("shows the ledger with the digest cut to 12 characters and the chain as intact", async () => {
    stubApi({ ...base, "GET /admin/evaluations?limit=200": EVALUATIONS });
    render(<ChangesPage />);
    openTab("評価台帳");
    expect(await screen.findByText(/鎖は正常です/)).toBeInTheDocument();
    expect(screen.getByText(/コマンドラインで実行/)).toBeInTheDocument();
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("run-7"); // newest first
    expect(rows[0]).toHaveTextContent("abcdef012345");
    expect(rows[0]).not.toHaveTextContent("abcdef0123456");
    expect(rows[1]).toHaveTextContent("判定不足");
  });

  it("warns loudly when the chain is broken, and says what to do when the ledger is empty", async () => {
    const mock = stubApi({ ...base, "GET /admin/evaluations?limit=200": { chain_intact: false, total: 0, entries: [] } });
    render(<ChangesPage />);
    openTab("評価台帳");
    expect(await screen.findByRole("alert")).toHaveTextContent("鎖が壊れています");
    expect(screen.getByText(/評価をコマンドラインで実行すると/)).toBeInTheDocument();
    expect(mock).toHaveBeenCalled();
  });

  it("lists the settings with limits, risk and the current value", async () => {
    stubApi({ ...base, "GET /admin/knobs": { knobs: KNOBS } });
    render(<ChangesPage />);
    openTab("設定項目");
    const guard = await screen.findByRole("row", { name: /RAG_HARD_MAX_DIST/ });
    expect(guard).toHaveTextContent("0.3 〜 1.5");
    expect(guard).toHaveTextContent("高");
    expect(screen.getByRole("row", { name: /MULTI_QUERY_ENABLED/ })).toHaveTextContent("オフ");
    expect(screen.getByText(/登録された項目だけです/)).toBeInTheDocument();
  });

  it("shows feedback by day with bars, the counts, and the changes in rollout with a link to the proposal", async () => {
    stubApi({
      ...base,
      "GET /admin/monitoring?days=14": MONITORING,
      "GET /admin/monitoring?days=30": { ...MONITORING, days: 30, changes_in_rollout: [] },
      "GET /admin/proposals/p-003": proposal({ id: "p-003", title: "ブースト調整", state: "canary" }, { id: "p-003", title: "ブースト調整", state: "canary" }),
    });
    render(<ChangesPage />);
    openTab("監視");
    const day = await screen.findByRole("row", { name: /2026-10-01/ });
    expect(day).toHaveTextContent("4");
    expect(screen.getByText("120")).toBeInTheDocument();
    expect(screen.getByText("回答を控えた数")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("期間"), { target: { value: "30" } });
    expect(await screen.findByText("展開中の変更はありません。")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("期間"), { target: { value: "14" } });
    fireEvent.click(await screen.findByRole("button", { name: /提案を開く: ブースト調整/ }));
    expect(await screen.findByRole("heading", { name: "ブースト調整" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "提案", selected: true })).toBeInTheDocument();
  });

  it("shows an error with a retry for the settings", async () => {
    stubApi({ ...base, "GET /admin/knobs": () => jsonResponse({ detail: "x" }, 500) });
    render(<ChangesPage />);
    openTab("設定項目");
    expect(await screen.findByRole("alert")).toHaveTextContent("取得できませんでした");
    expect(screen.getByRole("button", { name: "再読み込み" })).toBeInTheDocument();
  });
});

describe("the name of the person", () => {
  it("is filled in from the browser's memory, and the page works when storage fails", async () => {
    window.localStorage.setItem("console.actorName", "佐藤次郎");
    stubApi({ "GET /admin/proposals": LIST, "GET /admin/proposals/p-001": proposal() });
    render(<ChangesPage />);
    await openProposal(/開く: 距離のしきい値/);
    expect(await screen.findByLabelText(ACTOR_LABEL)).toHaveValue("佐藤次郎");
    cleanup();
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    render(<ChangesPage />);
    await openProposal(/開く: 距離のしきい値/);
    const field = await screen.findByLabelText(ACTOR_LABEL);
    expect(field).toHaveValue("");
    fireEvent.change(field, { target: { value: "田中太郎" } });
    expect(field).toHaveValue("田中太郎");
  });
});
