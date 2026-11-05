// Backups of the server's data: webapi/routers/backups.py (installation administrator only).
import { request } from "./client";

export interface BackupArchive {
  name: string;
  bytes: number;
  /** Seconds since the epoch. */
  modified: number;
}

export interface BackupList {
  archives: BackupArchive[];
  /** The id of the job that runs now, if any. */
  running: string | null;
  /** False when the server has no backup script (or no bash). */
  available: boolean;
}

export interface BackupJob {
  job_id: string;
  status: "running" | "ok" | "error";
  started_at: number;
  finished_at: number | null;
  actor: string;
  output: string;
  exit_code: number | null;
}

export const backupsApi = {
  list: () => request<BackupList>("/admin/backups"),
  start: (actor: string) =>
    request<{ job_id: string; status: string }>("/admin/backups", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ actor }) }),
  job: (id: string) => request<BackupJob>(`/admin/backups/jobs/${encodeURIComponent(id)}`),
};
