import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BackupsPage } from "../pages/Backups";
import { jsonResponse } from "./fixtures";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

type Handler = (init: RequestInit) => Response;

function routes(table: Record<string, Handler | unknown>) {
  const all: Record<string, Handler | unknown> = {
    "GET /admin/backups": { archives: [{ name: "chatbot_backup_20261101T090000Z.tar.gz", bytes: 5 * 1024 * 1024, modified: 1760000000 }], running: null, available: true },
    ...table,
  };
  const mock = vi.fn((url: string, init: RequestInit = {}) => {
    const key = `${(init.method ?? "GET").toUpperCase()} ${url.split("?")[0]}`;
    const entry = all[key];
    if (typeof entry === "function") return Promise.resolve((entry as Handler)(init));
    return Promise.resolve(entry === undefined ? jsonResponse({ detail: "no route" }, 404) : jsonResponse(entry));
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}

beforeEach(() => {
  document.cookie = "admin_csrf=token-123";
  window.sessionStorage.clear();
  vi.stubGlobal("location", { ...window.location, assign: vi.fn(), pathname: "/console/backups" });
});

describe("the backup page", () => {
  it("lists the archives and explains that a restore is not done from here", async () => {
    routes({});
    render(<BackupsPage />);
    const row = await screen.findByRole("row", { name: /chatbot_backup_20261101T090000Z\.tar\.gz/ });
    expect(row).toHaveTextContent("5.0 MB");
    expect(screen.getByText(/復元は、いま動いているデータを置き換えるため、画面からは行えません/)).toBeInTheDocument();
    expect(screen.getByText(/Qdrant・Postgres・Redis のデータは、このアーカイブに含まれません/)).toBeInTheDocument();
  });

  it("starts a backup with a name only, shows it running, then the result and the new list", async () => {
    let polls = 0;
    let listed = 0;
    const mock = routes({
      "GET /admin/backups": () => {
        listed++;
        return jsonResponse({ archives: listed > 1 ? [{ name: "chatbot_backup_new.tar.gz", bytes: 2048, modified: 1760001000 }] : [], running: null, available: true });
      },
      "POST /admin/backups": () => jsonResponse({ job_id: "job1", status: "running" }, 202),
      "GET /admin/backups/jobs/job1": () => {
        polls++;
        return jsonResponse({ job_id: "job1", status: polls < 2 ? "running" : "ok", started_at: 1, finished_at: polls < 2 ? null : 2, actor: "山田太郎", output: polls < 2 ? "" : "done", exit_code: polls < 2 ? null : 0 });
      },
    });
    render(<BackupsPage />);
    expect(await screen.findByText("アーカイブはまだありません。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "今すぐバックアップを取る" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "実行する" }));
    expect(await within(dialog).findByText("名前を入力してください。")).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText("あなたの名前（必須）"), { target: { value: "山田太郎" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "実行する" }));
    expect(await screen.findByText("バックアップを実行中です…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "今すぐバックアップを取る" })).toBeDisabled();
    expect(JSON.parse(String(mock.mock.calls.find(([u, i]) => String(u) === "/admin/backups" && (i as RequestInit)?.method === "POST")![1]!.body))).toEqual({ actor: "山田太郎" });
    expect(await screen.findByText("バックアップが完了しました。", {}, { timeout: 8000 })).toBeInTheDocument();
    expect(await screen.findByRole("row", { name: /chatbot_backup_new/ })).toBeInTheDocument();
    expect(screen.getByText("done")).toBeInTheDocument();
  }, 20000);

  it("says when a backup is already running, when the server has no script, and when it fails", async () => {
    routes({ "POST /admin/backups": () => jsonResponse({ detail: "busy" }, 409) });
    render(<BackupsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "今すぐバックアップを取る" }));
    fireEvent.change(within(screen.getByRole("dialog")).getByLabelText("あなたの名前（必須）"), { target: { value: "山田太郎" } });
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "実行する" }));
    expect(await screen.findByText("別のバックアップが実行中です。")).toBeInTheDocument();
    cleanup();
    routes({ "GET /admin/backups": { archives: [], running: null, available: false } });
    render(<BackupsPage />);
    expect(await screen.findByRole("note")).toHaveTextContent("バックアップのスクリプトがありません");
    expect(screen.getByRole("button", { name: "今すぐバックアップを取る" })).toBeDisabled();
  });

  it("follows a backup that is already running when the page opens", async () => {
    routes({
      "GET /admin/backups": { archives: [], running: "job9", available: true },
      "GET /admin/backups/jobs/job9": { job_id: "job9", status: "error", started_at: 1, finished_at: 2, actor: "x", output: "ERROR: disk full", exit_code: 3 },
    });
    render(<BackupsPage />);
    expect(await screen.findByText("バックアップに失敗しました。出力を確認してください。")).toBeInTheDocument();
    expect(await screen.findByText("ERROR: disk full")).toBeInTheDocument();
  });

  it("shows a clear message to an administrator who is not allowed", async () => {
    routes({ "GET /admin/backups": () => jsonResponse({ detail: "x" }, 403) });
    render(<BackupsPage />);
    expect(await screen.findByText("バックアップの情報を取得できませんでした。")).toBeInTheDocument();
  });
});
