import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ReviewQueuePage } from "../pages/ReviewQueue";
import { jsonResponse } from "./fixtures";
import { REVIEW_RESPONSE } from "./reviewQueueFixtures";

afterEach(cleanup);

beforeEach(() => {
  document.cookie = "admin_csrf=token-123";
  vi.stubGlobal("location", { ...window.location, assign: vi.fn(), pathname: "/console/review" });
});

function stub(handler: (url: string, init?: RequestInit) => Response) {
  const mock = vi.fn((url: string, init?: RequestInit) => Promise.resolve(handler(url, init)));
  vi.stubGlobal("fetch", mock);
  return mock;
}

describe("the review queue page", () => {
  it("shows the summary and every item with its fields", async () => {
    stub(() => jsonResponse(REVIEW_RESPONSE));
    render(<ReviewQueuePage />);
    expect(screen.getByRole("status")).toHaveTextContent("読み込み中");
    const card = await screen.findByRole("listitem", { name: "rv_001" });
    expect(within(card).getByText("経費精算の締め日はいつですか")).toBeInTheDocument();
    expect(within(card).getByText("faq_1, faq_2")).toBeInTheDocument();
    expect(within(card).getByText("high（高）")).toBeInTheDocument();
    expect(screen.getByText("runs/review/review_queue.jsonl")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("warns about malformed lines and explains an empty list", async () => {
    stub(() => jsonResponse({ ...REVIEW_RESPONSE, items: [], returned_count: 0, skipped_malformed_lines: 2 }));
    render(<ReviewQueuePage />);
    expect(await screen.findByText(/該当するレビュー項目はありません/)).toBeInTheDocument();
    expect(screen.getByText("2 件の不正な行を読み飛ばしました。")).toBeInTheDocument();
  });

  it("sends the filters on Refresh", async () => {
    const mock = stub(() => jsonResponse(REVIEW_RESPONSE));
    render(<ReviewQueuePage />);
    await screen.findByRole("listitem", { name: "rv_001" });
    expect(mock.mock.calls[0][0]).toBe("/admin/review/items?limit=100");
    fireEvent.change(screen.getByLabelText("Priority"), { target: { value: "high" } });
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "open" } });
    fireEvent.change(screen.getByLabelText("Tenant"), { target: { value: " default " } });
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(mock).toHaveBeenCalledTimes(2));
    expect(mock.mock.calls[1][0]).toBe("/admin/review/items?priority=high&status=open&tenant_id=default&limit=100");
  });

  it("shows an error and tries again", async () => {
    let calls = 0;
    stub(() => (++calls === 1 ? jsonResponse({ detail: "boom" }, 500) : jsonResponse(REVIEW_RESPONSE)));
    render(<ReviewQueuePage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("レビューキューを取得できませんでした");
    fireEvent.click(screen.getByRole("button", { name: "再読み込み" }));
    expect(await screen.findByRole("listitem", { name: "rv_001" })).toBeInTheDocument();
  });

  it("says calmly when the tenant is not allowed (403)", async () => {
    stub(() => jsonResponse({ detail: "forbidden" }, 403));
    render(<ReviewQueuePage />);
    expect(await screen.findByRole("note")).toHaveTextContent("権限がありません");
  });

  it("logs an action with the CSRF token and the exact payload", async () => {
    const mock = stub((url) =>
      url === "/admin/review/action" ? jsonResponse({ ok: true, stored: true, action_id: "action_9", review_id: "rv_001", action_type: "needs_new_faq" }) : jsonResponse(REVIEW_RESPONSE),
    );
    render(<ReviewQueuePage />);
    const card = await screen.findByRole("listitem", { name: "rv_001" });
    fireEvent.change(within(card).getByLabelText(/^Action/), { target: { value: "needs_new_faq" } });
    fireEvent.change(within(card).getByLabelText("Status after"), { target: { value: "needs_followup" } });
    fireEvent.change(within(card).getByLabelText("Tags"), { target: { value: " faq, , 経費 " } });
    fireEvent.change(within(card).getByLabelText("Operator note"), { target: { value: " 追加する " } });
    fireEvent.click(within(card).getByRole("button", { name: "Log action" }));
    expect(await within(card).findByText("記録しました（action_9）")).toBeInTheDocument();
    const [url, init] = mock.mock.calls[1] as [string, RequestInit];
    expect(url).toBe("/admin/review/action");
    expect(JSON.parse(init.body as string)).toEqual({
      review_id: "rv_001",
      action_type: "needs_new_faq",
      feedback_token: null,
      tenant_id: "default",
      selected_candidate_id: "faq_1",
      operator_note: "追加する",
      status_after: "needs_followup",
      tags: ["faq", "経費"],
    });
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("token-123");
  });

  it("asks for confirmation before logging a rejection, and the cancel sends nothing", async () => {
    const mock = stub(() => jsonResponse(REVIEW_RESPONSE));
    render(<ReviewQueuePage />);
    const card = await screen.findByRole("listitem", { name: "rv_002" });
    fireEvent.change(within(card).getByLabelText(/^Action/), { target: { value: "reject_candidate" } });
    fireEvent.click(within(card).getByRole("button", { name: "Log action" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "キャンセル" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mock).toHaveBeenCalledTimes(1);
  });

  it("shows a warning when the action was accepted but not stored, and a clear 403", async () => {
    let n = 0;
    stub((url) => {
      if (url !== "/admin/review/action") return jsonResponse(REVIEW_RESPONSE);
      return ++n === 1
        ? jsonResponse({ ok: true, stored: false, action_id: "a", review_id: "rv_001", action_type: "mark_resolved", warning: "review_action_logging_failed" })
        : jsonResponse({ detail: "review item belongs to another tenant" }, 403);
    });
    render(<ReviewQueuePage />);
    const card = await screen.findByRole("listitem", { name: "rv_001" });
    const submit = within(card).getByRole("button", { name: "Log action" });
    fireEvent.click(submit);
    expect(await within(card).findByRole("alert")).toHaveTextContent("保存されませんでした: review_action_logging_failed");
    fireEvent.click(submit);
    await waitFor(() => expect(within(card).getByRole("alert")).toHaveTextContent("別のテナントの項目です"));
  });
});
