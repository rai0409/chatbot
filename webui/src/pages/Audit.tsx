import { Fragment, useState } from "react";
import { ApiError } from "../api/client";
import { AUDIT_PAGE_SIZE, overviewApi, type AuditEvent, type AuditKind, type AuditVerify } from "../api/overview";
import { ErrorState, Loading } from "../components/Feedback";
import { Tabs } from "../components/Tabs";
import { jaOverview } from "../i18n/overview";
import { dateTime } from "../lib/format";
import { useLoad } from "../lib/useLoad";
import "./overview/overview.css";

const au = jaOverview.audit;
const KINDS: AuditKind[] = ["admin", "chat", "feedback"];
const HIDDEN = new Set(["timestamp", "action", "kind", "tenant_id", "hash", "prev_hash"]);

function summary(event: AuditEvent): string {
  const parts: string[] = [];
  for (const [key, value] of Object.entries(event)) {
    if (HIDDEN.has(key) || value === null || value === undefined) continue;
    if (typeof value === "object") continue;
    parts.push(`${key}=${String(value)}`);
  }
  const text = parts.join(" ");
  return text.length > 120 ? `${text.slice(0, 120)}…` : text;
}

const label = (event: AuditEvent) => String(event.action ?? event.kind ?? event.event ?? au.unknown);

function Verify({ kind }: { kind: AuditKind }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [bad, setBad] = useState(false);

  async function run() {
    setBusy(true);
    setMessage("");
    try {
      const r: AuditVerify = await overviewApi.auditVerify(kind);
      setBad(!r.ok);
      if (r.ok) setMessage(au.verifyOk(r.checked ?? 0));
      else if (r.error) setMessage(au.verifyError(r.error));
      else if (r.line !== undefined) setMessage(au.verifyBroken(String(r.file ?? "").split(/[\\/]/).pop() ?? "", r.line));
      else setMessage(au.verifyBrokenUnknown);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return;
      setBad(true);
      setMessage(e instanceof ApiError && e.status === 403 ? au.verifyForbidden : au.verifyFailed);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="ov-actions">
      <button type="button" disabled={busy} onClick={run}>
        {busy ? au.verifying : au.verify}
      </button>
      {message && (
        <p role="status" className={bad ? "error" : "notice"}>
          {message}
        </p>
      )}
    </div>
  );
}

function AuditList({ kind }: { kind: AuditKind }) {
  const [draftText, setDraftText] = useState("");
  const [draftAction, setDraftAction] = useState("");
  const [filter, setFilter] = useState({ text: "", action: "" });
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<number | null>(null);
  const [forbidden, setForbidden] = useState(false);

  const state = useLoad(
    async () => {
      setForbidden(false);
      try {
        return await overviewApi.audit({ kind, limit: AUDIT_PAGE_SIZE, offset, text: filter.text.trim(), action: filter.action.trim() });
      } catch (e) {
        if (e instanceof ApiError && e.status === 403) setForbidden(true);
        throw e;
      }
    },
    [kind, offset, filter],
    au.failed,
  );

  function apply(e: React.FormEvent) {
    e.preventDefault();
    setOffset(0);
    setOpen(null);
    setFilter({ text: draftText, action: draftAction });
  }

  function clear() {
    setDraftText("");
    setDraftAction("");
    setOffset(0);
    setOpen(null);
    setFilter({ text: "", action: "" });
  }

  const page = state.data;
  const from = page && page.events.length ? page.offset + 1 : 0;
  const to = page ? page.offset + page.events.length : 0;

  return (
    <div>
      <form className="ov-actions" onSubmit={apply}>
        <label>
          {au.text}
          <input type="search" value={draftText} onChange={(e) => setDraftText(e.target.value)} />
        </label>
        <label>
          {au.action}
          <input type="text" value={draftAction} onChange={(e) => setDraftAction(e.target.value)} />
        </label>
        <button type="submit">{au.search}</button>
        <button type="button" onClick={clear}>
          {au.clear}
        </button>
      </form>
      <Verify kind={kind} />
      {state.error ? (
        forbidden ? (
          <p role="alert" className="error">
            {au.forbidden}
          </p>
        ) : (
          <ErrorState message={au.failed} onRetry={state.reload} />
        )
      ) : !page ? (
        state.loading && <Loading />
      ) : page.events.length === 0 ? (
        <p className="empty">{au.empty}</p>
      ) : (
        <>
          <p className="muted" aria-live="polite">
            {au.range(from, to, page.total)}
          </p>
          <div className="ov-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">{au.colTime}</th>
                  <th scope="col">{au.colAction}</th>
                  <th scope="col">{au.colTenant}</th>
                  <th scope="col">{au.colSummary}</th>
                  <th scope="col">{au.colDetail}</th>
                </tr>
              </thead>
              <tbody>
                {page.events.map((event, i) => (
                  <Fragment key={`${page.offset}-${i}`}>
                    <tr>
                      <th scope="row">{dateTime(String(event.timestamp ?? ""))}</th>
                      <td>{label(event)}</td>
                      <td>{String(event.tenant_id ?? "") || jaOverview.common.none}</td>
                      <td>{summary(event)}</td>
                      <td>
                        <button type="button" aria-expanded={open === i} onClick={() => setOpen(open === i ? null : i)}>
                          {open === i ? au.hide : au.show}
                        </button>
                      </td>
                    </tr>
                    {open === i && (
                      <tr>
                        <td colSpan={5}>
                          <pre className="ov-pre">{JSON.stringify(event, null, 2)}</pre>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
          <div className="ov-actions">
            <button type="button" disabled={offset === 0} onClick={() => { setOffset(Math.max(0, offset - AUDIT_PAGE_SIZE)); setOpen(null); }}>
              {au.prev}
            </button>
            <button type="button" disabled={offset + AUDIT_PAGE_SIZE >= page.total} onClick={() => { setOffset(offset + AUDIT_PAGE_SIZE); setOpen(null); }}>
              {au.next}
            </button>
          </div>
        </>
      )}
    </div>
  );
}

export function AuditPage() {
  return (
    <section>
      <h1>{au.title}</h1>
      <p className="muted">{au.intro}</p>
      <p role="note" className="notice">
        {au.protect}
      </p>
      <Tabs tabs={KINDS.map((kind) => ({ id: kind, label: au.kinds[kind], content: <AuditList key={kind} kind={kind} /> }))} />
    </section>
  );
}
