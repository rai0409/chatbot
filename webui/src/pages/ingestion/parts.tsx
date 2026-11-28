import { Dialog } from "../../components/Dialog";
import type { Report } from "../../api/ingestion";
import { ji } from "../../i18n/ingestion";
import { describeError, formatDetail } from "../../lib/errors";

/** The outcome of a request: the report, or the message of the failure. */
export interface Outcome {
  data?: Report;
  error?: string;
}

/** The message for a failed change: the permission and conflict cases get their own words, the rest shows the server's reason. */
export function failureMessage(e: unknown, fallback: string): string {
  return describeError(e, fallback, {
    403: ji.common.forbidden,
    409: (err) => `${ji.common.conflict}（${err.detail}）`,
    404: (err) => `${ji.common.notFound}（${err.detail}）`,
  });
}

export function lines(text: string): string[] {
  return text
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
}

export function statusClass(status: string): string {
  if (["ok", "succeeded", "completed"].includes(status)) return "ok";
  if (["error", "failed", "rejected", "interrupted"].includes(status)) return "failing";
  return "disabled";
}

export function StatusBadge({ status }: { status: string }) {
  return <span className={`badge ${statusClass(status)}`}>{ji.status[status] ?? status}</span>;
}

/** A confirmation before something that cannot be undone or changes the served data. Focus starts on the safe button. */
export function ConfirmDialog({ title, busy, confirmLabel = ji.common.confirm, onConfirm, onCancel }: { title: string; busy?: boolean; confirmLabel?: string; onConfirm: () => void; onCancel: () => void }) {
  return (
    <Dialog title={title} onCancel={onCancel} busy={busy}>
      <button type="button" className="danger" disabled={busy} onClick={onConfirm}>
        {confirmLabel}
      </button>
      <button type="button" data-autofocus onClick={onCancel}>
        {ji.common.cancel}
      </button>
    </Dialog>
  );
}

/** What the server answered: the scalar values as chips, a problem message, a "done" message, and the whole JSON on demand. */
export function ResultView({ outcome, label }: { outcome: Outcome | null; label: string }) {
  if (!outcome) return null;
  const res = outcome.data;
  const scalars = res
    ? Object.keys(res)
        .filter((k) => res[k] !== null && typeof res[k] !== "object" && k !== "error" && k !== "detail")
        .slice(0, 12)
    : [];
  const raw = res?.error || res?.detail;
  const problem = outcome.error ?? (raw ? formatDetail(raw) : "");
  const finished = res && ["succeeded", "completed"].includes(String(res.status)) ? true : Boolean(res?.ok);
  return (
    <div className="result" role="region" aria-label={label}>
      {scalars.length > 0 && (
        <ul className="summary" style={{ display: "flex", gap: 8, flexWrap: "wrap", listStyle: "none", padding: 0 }}>
          {scalars.map((k) => (
            <li key={k} className="badge disabled">
              {ji.keys[k] ?? k}: <b>{k === "status" ? ji.status[String(res?.[k])] ?? String(res?.[k]) : String(res?.[k])}</b>
            </li>
          ))}
        </ul>
      )}
      {problem && (
        <p role="alert" className="error">
          {problem}
        </p>
      )}
      {!problem && finished && (
        <p role="status" className="notice">
          {ji.common.done}
        </p>
      )}
      {res && (
        <details>
          <summary>{ji.common.details}</summary>
          <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-all", maxHeight: 360, overflow: "auto" }}>{JSON.stringify(res, null, 2)}</pre>
        </details>
      )}
    </div>
  );
}
