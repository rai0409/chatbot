import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, request } from "../api/client";
import { describeError, formatDetail } from "../lib/errors";
import { jsonResponse } from "./fixtures";

afterEach(() => vi.unstubAllGlobals());

describe("describeError", () => {
  it("lists a FastAPI validation answer in Japanese, by field", async () => {
    const detail = [
      { type: "missing", loc: ["body", "tenant_id"], msg: "Field required" },
      { type: "string_too_long", loc: ["body", "question"], msg: "String should have at most 300 characters", ctx: { max_length: 300 } },
      { type: "greater_than_equal", loc: ["query", "days"], msg: "Input should be greater than or equal to 1", ctx: { ge: 1 } },
      { type: "value_error", loc: ["body", "items", 0, "pages"], msg: "Value error, pages must be numbers" },
    ];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail }, 422)));
    const error = await request("/admin/x").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).body).toEqual(detail);
    const text = describeError(error, "保存できませんでした");
    expect(text).toBe(
      "保存できませんでした: tenant_id: 必須です / question: 長すぎます（最大 300） / days: 1 以上にしてください / pages: pages must be numbers",
    );
    expect(text).not.toContain("{");
  });

  it("keeps a 400 with a text detail, and says the fixed sentence for the other statuses", () => {
    expect(describeError(new ApiError(400, "bad id"), "失敗しました")).toBe("失敗しました: bad id");
    expect(describeError(new ApiError(401, "unauthorized"), "x")).toContain("ログインが必要です");
    expect(describeError(new ApiError(403, "no"), "x")).toBe("権限がありません。");
    expect(describeError(new ApiError(404, "no"), "x")).toContain("見つかりません");
    expect(describeError(new ApiError(409, "stale"), "x")).toContain("競合: 他の操作で更新されました");
    expect(describeError(new ApiError(413, "big"), "x")).toContain("サイズ超過");
    expect(describeError(new ApiError(429, "slow"), "x")).toContain("しばらく待って");
    expect(describeError(new ApiError(503, "Traceback ..."), "取得できませんでした")).toBe(
      "取得できませんでした: サーバーでエラーが発生しました。しばらく待ってから、もう一度お試しください。",
    );
    expect(describeError(new Error("odd"), "取得できませんでした")).toBe("取得できませんでした");
  });

  it("lets a screen keep its own sentence for a status", () => {
    expect(describeError(new ApiError(409, "x"), "f", { 409: "先に変更されました" })).toBe("先に変更されました");
    expect(describeError(new ApiError(404, "gone"), "f", { 404: (e) => `ない（${e.detail}）` })).toBe("ない（gone）");
  });

  it("turns a failure to reach the server into ApiError(0) with the network sentence", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const error = await request("/admin/x").catch((e: unknown) => e);
    expect(error).toMatchObject({ status: 0 });
    expect(describeError(error, "x")).toContain("サーバーに接続できませんでした");
  });

  it("formats other detail shapes without raw JSON when it can", () => {
    expect(formatDetail({ message: "plan changed" })).toBe("plan changed");
    expect(formatDetail(null)).toBe("");
    expect(formatDetail({ a: 1 })).toBe('{"a":1}');
  });
});
