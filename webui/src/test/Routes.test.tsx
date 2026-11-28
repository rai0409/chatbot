import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { lazy, Suspense } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { Loading } from "../components/Feedback";
import { ScreenBoundary, isChunkLoadError } from "../components/ScreenBoundary";
import { TenantProvider } from "../lib/tenant";
import { jsonResponse } from "./fixtures";

afterEach(cleanup);
beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse({ detail: "not here" }, 404))));
  vi.spyOn(console, "error").mockImplementation(() => undefined);
});

describe("the routes", () => {
  it("loads a screen on first use behind the shell, and names the browser tab after it", async () => {
    render(
      <MemoryRouter initialEntries={["/access"]}>
        <TenantProvider load={() => Promise.reject(new Error("no"))}>
          <AppRoutes />
        </TenantProvider>
      </MemoryRouter>,
    );
    expect(screen.getByRole("navigation")).toBeInTheDocument(); // the shell is there at once
    expect(await screen.findByRole("heading", { level: 1, name: "権限の確認" })).toBeInTheDocument();
    expect(document.title).toBe("権限の確認 | 管理コンソール");
  });
});

function failingScreen(error: Error) {
  const Screen = lazy(() => Promise.reject(error));
  return (
    <ScreenBoundary onReload={reload}>
      <Suspense fallback={<Loading />}>
        <Screen />
      </Suspense>
    </ScreenBoundary>
  );
}
const reload = vi.fn();

describe("a screen that cannot be shown", () => {
  it("asks for a reload when the screen's chunk is gone (a new release)", async () => {
    render(failingScreen(new TypeError("Failed to fetch dynamically imported module: /console/assets/Usage-old.js")));
    expect(screen.getByRole("status")).toHaveTextContent("読み込み中");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("新しい版が公開されました。再読み込みしてください");
    fireEvent.click(screen.getByRole("button", { name: "再読み込みする" }));
    expect(reload).toHaveBeenCalled();
  });

  it("says that it failed for any other error", async () => {
    render(failingScreen(new Error("boom")));
    expect(await screen.findByRole("alert")).toHaveTextContent("画面を表示できませんでした");
  });

  it("tells a chunk failure from other errors", () => {
    expect(isChunkLoadError(new TypeError("error loading dynamically imported module"))).toBe(true);
    expect(isChunkLoadError(new TypeError("Importing a module script failed."))).toBe(true);
    expect(isChunkLoadError(new Error("boom"))).toBe(false);
    expect(isChunkLoadError("text")).toBe(false);
  });
});
