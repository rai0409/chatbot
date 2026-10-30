import { useState } from "react";
import { ApiError } from "../api/client";
import { goldenApi, type GoldenCase } from "../api/golden";
import { ErrorState, Loading } from "../components/Feedback";
import { jaGolden } from "../i18n/golden";
import { dateTime } from "../lib/format";
import { useLoad } from "../lib/useLoad";
import { CaseForm } from "./golden/CaseForm";
import "./golden/golden.css";

const g = jaGolden;
const TENANT_KEY = "console.goldenTenant";

function savedTenant(): string {
  try {
    return window.localStorage.getItem(TENANT_KEY) || "default";
  } catch {
    return "default";
  }
}

function rememberTenant(value: string) {
  try {
    window.localStorage.setItem(TENANT_KEY, value);
  } catch {
    /* storage is not available: the page works without it */
  }
}

function originText(origin: string | undefined): string {
  if (!origin) return "—";
  if (origin.startsWith("record:")) return g.origin.record;
  return origin === "manual" ? g.origin.manual : origin;
}

type Filter = "all" | "enabled" | "disabled";
type Form = { editing: GoldenCase | null } | null;

export function GoldenPage() {
  const [draft, setDraft] = useState(savedTenant);
  const [tenant, setTenant] = useState(savedTenant);
  const [filter, setFilter] = useState<Filter>("all");
  const [form, setForm] = useState<Form>(null);
  const [removing, setRemoving] = useState<GoldenCase | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [problem, setProblem] = useState("");
  const { data, error, loading, reload } = useLoad(() => goldenApi.list(tenant), [tenant], g.loadFailed);

  function commitTenant(event: React.FormEvent) {
    event.preventDefault();
    const value = draft.trim() || "default";
    rememberTenant(value);
    setDraft(value);
    setForm(null);
    setNotice("");
    setProblem("");
    setTenant(value);
  }

  function describe(e: unknown, fallback: string): string {
    if (e instanceof ApiError) {
      if (e.status === 403) return g.notAllowed;
      if (e.status === 409) return g.conflict;
      if (e.status === 404) return g.notFound;
      if (e.status === 400 && e.detail) return `${fallback}: ${e.detail}`;
    }
    return fallback;
  }

  async function toggle(item: GoldenCase) {
    setBusy(true);
    setNotice("");
    setProblem("");
    try {
      await goldenApi.update(tenant, item.case_id, { enabled: !(item.enabled ?? true), expected_updated_at: item.updated_at });
      setNotice(g.changed);
      reload();
    } catch (e) {
      setProblem(describe(e, g.changeFailed));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!removing) return;
    setBusy(true);
    setNotice("");
    setProblem("");
    try {
      await goldenApi.remove(tenant, removing.case_id);
      setNotice(g.deleted);
      reload();
    } catch (e) {
      setProblem(describe(e, g.changeFailed));
    } finally {
      setRemoving(null);
      setBusy(false);
    }
  }

  async function exportJson() {
    setBusy(true);
    setNotice("");
    setProblem("");
    try {
      const body = await goldenApi.export(tenant);
      const url = URL.createObjectURL(new Blob([JSON.stringify(body, null, 2)], { type: "application/json" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `golden-qa-${tenant}.json`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      setNotice(g.exported);
    } catch (e) {
      setProblem(describe(e, g.exportFailed));
    } finally {
      setBusy(false);
    }
  }

  const shown = (data?.cases ?? []).filter((c) => (filter === "all" ? true : filter === "enabled" ? (c.enabled ?? true) : !(c.enabled ?? true)));

  return (
    <section>
      <h1>{g.title}</h1>
      <p className="muted">{g.intro}</p>
      <form className="toolbar" onSubmit={commitTenant}>
        <label>
          {g.tenant}
          <input value={draft} maxLength={64} onChange={(e) => setDraft(e.target.value)} aria-describedby="golden-tenant-help" />
        </label>
        <button type="submit">{g.load}</button>
      </form>
      <p id="golden-tenant-help" className="muted">
        {g.tenantHelp}
      </p>

      {notice && (
        <p role="status" className="notice">
          {notice}
        </p>
      )}
      {problem && (
        <p role="alert" className="error">
          {problem}
        </p>
      )}
      {loading && !data && <Loading />}
      {error && <ErrorState message={error} onRetry={reload} />}

      {data && !error && (
        <>
          <div className="toolbar">
            <span>{g.summary(data.total, data.enabled)}</span>
            <label>
              {g.filter}
              <select value={filter} onChange={(e) => setFilter(e.target.value as Filter)}>
                <option value="all">{g.filterAll}</option>
                <option value="enabled">{g.filterEnabled}</option>
                <option value="disabled">{g.filterDisabled}</option>
              </select>
            </label>
            <button type="button" disabled={busy} onClick={() => setForm({ editing: null })}>
              {g.create}
            </button>
            <button type="button" disabled={busy} onClick={() => void exportJson()}>
              {g.export}
            </button>
          </div>
          <p className="muted">{g.exportHelp}</p>

          {data.cases.length === 0 ? (
            <p className="empty">{g.empty}</p>
          ) : shown.length === 0 ? (
            <p className="empty">{g.emptyFilter}</p>
          ) : (
            <table className="golden-table">
              <thead>
                <tr>
                  <th scope="col">{g.col.id}</th>
                  <th scope="col">{g.col.question}</th>
                  <th scope="col">{g.col.enabled}</th>
                  <th scope="col">{g.col.priority}</th>
                  <th scope="col">{g.col.origin}</th>
                  <th scope="col">{g.col.updated}</th>
                  <th scope="col">{g.col.action}</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((item) => {
                  const on = item.enabled ?? true;
                  return (
                    <tr key={item.case_id}>
                      <th scope="row">{item.case_id}</th>
                      <td className="q">{item.question}</td>
                      <td>
                        <span className={`badge ${on ? "ok" : "disabled"}`}>{on ? g.enabledOn : g.enabledOff}</span>
                      </td>
                      <td>{item.priority ?? 0}</td>
                      <td>{originText(item.origin)}</td>
                      <td>{dateTime(item.updated_at)}</td>
                      <td>
                        <div className="golden-actions">
                          <button type="button" disabled={busy} aria-label={g.editFor(item.case_id)} onClick={() => setForm({ editing: item })}>
                            {g.edit}
                          </button>
                          <button
                            type="button"
                            disabled={busy}
                            aria-label={g.turnFor(on ? g.turnOff : g.turnOn, item.case_id)}
                            onClick={() => void toggle(item)}
                          >
                            {on ? g.turnOff : g.turnOn}
                          </button>
                          <button type="button" disabled={busy} aria-label={g.removeFor(item.case_id)} onClick={() => setRemoving(item)}>
                            {g.remove}
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </>
      )}

      {form && (
        <CaseForm
          key={form.editing?.case_id ?? "new"}
          tenant={tenant}
          editing={form.editing}
          onDone={(message) => {
            setForm(null);
            setNotice(message);
            setProblem("");
            reload();
          }}
          onCancel={() => setForm(null)}
          onReload={() => {
            setForm(null);
            reload();
          }}
        />
      )}

      {removing && (
        <div role="dialog" aria-modal="true" aria-labelledby="golden-delete-title" className="dialog">
          <h2 id="golden-delete-title">{g.deleteTitle(removing.case_id)}</h2>
          <p>{removing.question}</p>
          <p className="muted">{g.deleteBody}</p>
          <button type="button" disabled={busy} onClick={() => void remove()}>
            {g.deleteConfirm}
          </button>{" "}
          <button type="button" onClick={() => setRemoving(null)}>
            {g.cancel}
          </button>
        </div>
      )}
    </section>
  );
}
