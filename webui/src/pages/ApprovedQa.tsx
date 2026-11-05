import { useState, type FormEvent } from "react";
import { ApiError } from "../api/client";
import { PAGE_SIZE, type QaFilter, type QaHistoryRow, type QaRecord, type QaRow, type QaSave, type QaStatus, approvedQaApi } from "../api/approvedQa";
import { ReasonActorDialog } from "../components/ReasonActorDialog";
import { ErrorState, Loading } from "../components/Feedback";
import { jaApprovedQa as q } from "../i18n/approvedQa";
import { dateTime, number } from "../lib/format";
import { useLoad } from "../lib/useLoad";
import { ImportPanel } from "./approvedQa/ImportPanel";
import { RecordForm } from "./approvedQa/RecordForm";

const FIRST: QaFilter = { tenant: "", status: "", query: "", offset: 0 };

type Dialog = { kind: "status"; row: QaRow; status: QaStatus } | { kind: "delete"; row: QaRow };
type Panel = { kind: "form"; record: QaRecord | null; history: QaHistoryRow[] } | { kind: "import" } | null;

function failure(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 409) return q.conflict;
    if (e.status === 404) return q.notFound;
    if (e.status === 403) return q.forbidden;
    if (e.status === 400 && e.detail) return q.failedWith(e.detail);
  }
  return q.failed;
}

const preview = (text: string, n = 60) => (text.length > n ? `${text.slice(0, n)}…` : text);

export function ApprovedQaPage() {
  const [tenantInput, setTenantInput] = useState("");
  const [statusInput, setStatusInput] = useState("");
  const [queryInput, setQueryInput] = useState("");
  const [filter, setFilter] = useState<QaFilter>(FIRST);
  const [panel, setPanel] = useState<Panel>(null);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  const [formProblem, setFormProblem] = useState("");

  const list = useLoad(() => approvedQaApi.list(filter), [filter.tenant, filter.status, filter.query, filter.offset], q.loadFailed);
  const health = useLoad(approvedQaApi.health, [], q.loadFailed);

  function apply(event: FormEvent) {
    event.preventDefault();
    const next = { tenant: tenantInput, status: statusInput, query: queryInput, offset: 0 };
    if (JSON.stringify(next) === JSON.stringify(filter)) list.reload();
    else setFilter(next);
  }

  function refresh() {
    list.reload();
    health.reload();
  }

  async function openForm(row: QaRow | null) {
    setNotice(null);
    setFormProblem("");
    if (!row) return setPanel({ kind: "form", record: null, history: [] });
    try {
      const found = await approvedQaApi.record(row.tenant_id, row.qa_id);
      setPanel({ kind: "form", record: found.record, history: found.history });
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) setNotice({ ok: false, text: failure(e) });
    }
  }

  async function save(body: QaSave) {
    setBusy(true);
    setFormProblem("");
    try {
      const saved = await approvedQaApi.save(body);
      setNotice({ ok: true, text: saved.reverted_to_draft ? q.savedBackToDraft : q.saved });
      setPanel(null);
      refresh();
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) setFormProblem(failure(e));
    } finally {
      setBusy(false);
    }
  }

  async function confirm(reason: string, actor: string) {
    if (!dialog) return;
    setBusy(true);
    try {
      if (dialog.kind === "delete") {
        await approvedQaApi.remove({ tenant_id: dialog.row.tenant_id, qa_id: dialog.row.qa_id, reason, actor });
        setNotice({ ok: true, text: q.deleted });
      } else {
        await approvedQaApi.setStatus({ tenant_id: dialog.row.tenant_id, qa_id: dialog.row.qa_id, status: dialog.status, reason, actor });
        setNotice({ ok: true, text: q.statusChanged(q.statuses[dialog.status]) });
      }
      refresh();
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) setNotice({ ok: false, text: failure(e) });
    } finally {
      setBusy(false);
      setDialog(null);
    }
  }

  const data = list.data;
  const problems = health.data;
  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <section>
      <h1>{q.title}</h1>
      <p>{q.intro}</p>
      {data && !data.enabled && (
        <p role="note" className="warn">
          {q.notEnabled}
        </p>
      )}
      {problems?.path_is_default && (
        <p role="alert" className="error">
          {q.pathNotSet}
        </p>
      )}
      {data?.require_approver && <p className="muted">{q.requireApprover}</p>}
      {problems && problems.unreadable_lines > 0 && (
        <p role="alert" className="error">
          {q.unreadable(problems.unreadable_lines)}
        </p>
      )}
      {problems && problems.errors.length > 0 && (
        <details className="warn">
          <summary>{q.healthProblems(problems.errors.length)}</summary>
          <ul>
            {problems.errors.map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>
        </details>
      )}

      <form className="toolbar" onSubmit={apply}>
        <label>
          {q.tenant}
          <input value={tenantInput} onChange={(e) => setTenantInput(e.target.value)} maxLength={64} size={14} aria-describedby="qa-tenant-hint" />
        </label>
        <label>
          {q.status}
          <select value={statusInput} onChange={(e) => setStatusInput(e.target.value)}>
            <option value="">{q.allStatuses}</option>
            {(["draft", "approved", "rejected"] as QaStatus[]).map((s) => (
              <option key={s} value={s}>
                {q.statuses[s]}
              </option>
            ))}
          </select>
        </label>
        <label>
          {q.search}
          <input value={queryInput} onChange={(e) => setQueryInput(e.target.value)} maxLength={200} size={24} />
        </label>
        <button type="submit">{q.load}</button>
      </form>
      <p id="qa-tenant-hint" className="muted">
        {q.tenantHint}
      </p>
      <div className="toolbar">
        <button type="button" onClick={() => void openForm(null)}>
          {q.create}
        </button>
        <button type="button" onClick={() => setPanel({ kind: "import" })}>
          {q.importCsv}
        </button>
        <a href={approvedQaApi.exportUrl(filter.tenant, filter.status)}>{q.exportJsonl}</a>
        <a href={approvedQaApi.templateUrl}>{q.template}</a>
      </div>
      {notice && (
        <p role={notice.ok ? "status" : "alert"} className={notice.ok ? "notice" : "error"}>
          {notice.text}
        </p>
      )}

      {panel?.kind === "form" && (
        <RecordForm
          key={panel.record?.qa_id ?? "new"}
          tenantDefault={filter.tenant}
          record={panel.record}
          history={panel.history}
          busy={busy}
          problem={formProblem}
          onSave={(body) => void save(body)}
          onClose={() => setPanel(null)}
        />
      )}
      {panel?.kind === "import" && <ImportPanel tenantDefault={filter.tenant} onImported={refresh} onClose={() => setPanel(null)} />}

      <section className="panel" aria-labelledby="qa-list">
        <h2 id="qa-list">{q.title}</h2>
        {list.error ? (
          <ErrorState message={list.error} onRetry={list.reload} />
        ) : !data ? (
          <Loading />
        ) : (
          <>
            <p className="muted" role="status">
              {q.counts(data.counts.draft, data.counts.approved, data.counts.rejected)}　{q.total(data.total)}
            </p>
            {data.rows.length === 0 ? (
              <p className="empty">{q.empty}</p>
            ) : (
              <div style={{ overflowX: "auto" }}>
                <table>
                  <thead>
                    <tr>
                      <th scope="col">{q.colQuestion}</th>
                      <th scope="col">{q.colAnswer}</th>
                      <th scope="col">{q.colStatus}</th>
                      <th scope="col">{q.colTags}</th>
                      <th scope="col">{q.colAliases}</th>
                      <th scope="col">{q.colCitations}</th>
                      <th scope="col">{q.colUpdated}</th>
                      <th scope="col">{q.colAction}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.rows.map((row) => (
                      <tr key={`${row.tenant_id}/${row.qa_id}`}>
                        <th scope="row">
                          {preview(row.question)}
                          {data.can_see_all && <span className="muted"> [{row.tenant_id}]</span>}
                        </th>
                        <td>{preview(row.approved_answer)}</td>
                        <td>{q.statuses[row.status] ?? row.status}</td>
                        <td>{row.tags.join(", ")}</td>
                        <td>{number(row.aliases.length)}</td>
                        <td>{number(row.citations)}</td>
                        <td>{q.updatedBy(dateTime(row.updated_at), row.updated_by ?? "")}</td>
                        <td>
                          <button type="button" aria-label={`${q.edit}: ${preview(row.question, 30)}`} onClick={() => void openForm(row)}>
                            {q.edit}
                          </button>{" "}
                          {row.status !== "approved" && (
                            <button type="button" aria-label={`${q.approve}: ${preview(row.question, 30)}`} onClick={() => setDialog({ kind: "status", row, status: "approved" })}>
                              {q.approve}
                            </button>
                          )}{" "}
                          {row.status === "approved" && (
                            <button type="button" aria-label={`${q.toDraft}: ${preview(row.question, 30)}`} onClick={() => setDialog({ kind: "status", row, status: "draft" })}>
                              {q.toDraft}
                            </button>
                          )}{" "}
                          {row.status !== "rejected" && (
                            <button type="button" aria-label={`${q.reject}: ${preview(row.question, 30)}`} onClick={() => setDialog({ kind: "status", row, status: "rejected" })}>
                              {q.reject}
                            </button>
                          )}{" "}
                          {row.status !== "approved" && (
                            <button type="button" aria-label={`${q.remove}: ${preview(row.question, 30)}`} onClick={() => setDialog({ kind: "delete", row })}>
                              {q.remove}
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {data.total > PAGE_SIZE && (
              <div className="toolbar">
                <button type="button" disabled={filter.offset === 0} onClick={() => setFilter({ ...filter, offset: Math.max(0, filter.offset - PAGE_SIZE) })}>
                  {q.prev}
                </button>
                <span className="muted">
                  {Math.floor(filter.offset / PAGE_SIZE) + 1} / {pages}
                </span>
                <button type="button" disabled={filter.offset + PAGE_SIZE >= data.total} onClick={() => setFilter({ ...filter, offset: filter.offset + PAGE_SIZE })}>
                  {q.next}
                </button>
              </div>
            )}
          </>
        )}
      </section>

      {dialog && (
        <ReasonActorDialog
          title={dialog.kind === "delete" ? q.deleteDialog : q.statusDialog(q.statuses[dialog.status])}
          body={dialog.kind === "delete" ? q.deleteBody : q.statusBody[dialog.status]}
          confirmLabel={dialog.kind === "delete" ? q.remove : q.save}
          cancelLabel={q.cancel}
          reasonLabel={q.reason}
          actorLabel={q.actor}
          needBoth={q.needReason}
          busy={busy}
          onConfirm={(r, a) => void confirm(r, a)}
          onCancel={() => setDialog(null)}
        />
      )}
    </section>
  );
}
