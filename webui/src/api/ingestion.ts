// Calls of the document ingestion screen (webapi/main.py, "Document ingestion UI + job status").
import { ApiError, LOGIN_URL, csrfToken, request } from "./client";

/** A report from the server. Its keys depend on the job, so it is kept open and shown as it is. */
export type Report = Record<string, unknown>;

export interface IngestionJob extends Report {
  job_id: string;
  status: string;
  mode?: string;
  expected_tenant?: string | null;
  collection?: string | null;
  /** Seconds since the epoch. */
  created_at?: number;
}

export interface UploadResult {
  path: string;
  filename: string;
  bytes: number;
  sha256: string;
}

export interface DryRunBody {
  inputs: string[];
  expected_tenant: string | null;
  collection: string | null;
}

export interface RawDocumentsBody {
  inputs: string[];
  expected_tenant: string;
  collection: string;
  execute: boolean;
  async_job: boolean;
}

export interface PromoteBody {
  tenant_id: string;
  collection: string;
  execute: boolean;
}

export interface DeleteBody {
  tenant_id: string;
  source_docs: string[];
  collection: string | null;
  execute: boolean;
}

const JSON_HEADERS = { "Content-Type": "application/json" };

function post<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify(body) });
}

/** The file goes as the raw body (no multipart), with the tenant it is stored for. */
async function upload(file: File, tenant: string): Promise<UploadResult> {
  const query = `filename=${encodeURIComponent(file.name)}&tenant=${encodeURIComponent(tenant)}`;
  const response = await fetch(`/admin/ingestion/upload?${query}`, {
    method: "PUT",
    headers: { "Content-Type": "application/octet-stream", "X-CSRF-Token": csrfToken(), Accept: "application/json" },
    credentials: "same-origin",
    body: file,
  });
  if (response.status === 401) {
    window.location.assign(`${LOGIN_URL}?next=${encodeURIComponent(window.location.pathname)}`);
    throw new ApiError(401, "unauthorized");
  }
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as UploadResult;
}

export const ingestionApi = {
  dryRun: (body: DryRunBody) => post<IngestionJob>("/admin/ingestion/dry-run", body),
  rawDocuments: (body: RawDocumentsBody) => post<Report>("/admin/ingestion/raw-documents", body),
  upload,
  jobs: () => request<{ jobs: IngestionJob[] }>("/admin/ingestion/jobs"),
  job: (id: string) => request<IngestionJob>(`/admin/ingestion/jobs/${encodeURIComponent(id)}`),
  promote: (body: PromoteBody) => post<Report>("/admin/ingestion/promote", body),
  deleteDocuments: (body: DeleteBody) => post<Report>("/admin/ingestion/documents/delete", body),
};
