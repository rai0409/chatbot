import { type FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import { type ApplyResult, type ChangeEntry, type CreateBody, type ScanResult, type SourcePlan, type SourceRow, sourcesApi } from "../api/sources";
import { ErrorState, Loading } from "../components/Feedback";
import { reasonText } from "../i18n/ja";
import { js } from "../i18n/sources";
import { dateTime } from "../lib/format";
import { useLoad } from "../lib/useLoad";
import { ConfirmDialog } from "./ingestion/parts";

type Panel =
  | { type: "scan"; id: string; result: ScanResult }
  | { type: "plan"; id: string; plan: SourcePlan }
  | { type: "changes"; id: string; entries: ChangeEntry[] }
  | { type: "apply"; id: string; result: ApplyResult };

type Confirm = { kind: "remove"; id: string } | { kind: "apply"; id: string; deleted: number } | { kind: "held"; id: string; sha: string; reasons: string[] };

interface Message {
  ok: boolean;
  text: string;
}

const INTEGRATION_OFF = "integration_off:";

function guardText(reasons: string[]): string {
  return reasons.map((r) => js.guardReason[r] ?? r).join("、");
}

/** The failure of a request in words: 403 and 409 and 404 have their own text, the rest shows the server's reason. */
function failure(e: unknown, prefix: string): string {
  if (e instanceof ApiError) {
    if (e.status === 403) return js.msg.forbidden;
    if (e.status === 409) return `${js.msg.conflict}（${e.detail}）`;
    if (e.status === 404) return js.msg.notFound;
    return `${prefix}（${e.detail}）`;
  }
  return prefix;
}

function IntegrationOff({ reason }: { reason: string }) {
  return (
    <div role="status" className="panel">
      <p>
        <strong>{js.integrationOff.title}</strong>
        {reason && <span>: {reasonText(reason)}</span>}
      </p>
      <p className="muted">{js.integrationOff.note}</p>
      <Link to="/system">{js.integrationOff.link}</Link>
    </div>
  );
}

function lastScan(row: SourceRow) {
  const error = row.state.last_error;
  if (!error) return dateTime(row.state.last_scan_at);
  if (error.startsWith(INTEGRATION_OFF)) return `${js.integrationOff.title}（${reasonText(error.slice(INTEGRATION_OFF.length))}）`;
  return error === "source_unavailable" ? js.msg.sourceUnavailable : error;
}

function pendingText(row: SourceRow): string {
  if (!row.pending) return js.notScanned;
  const text = js.pendingText(row.pending.added, row.pending.modified, row.pending.deleted);
  return row.guard?.hold ? `${text}　${js.heldText}: ${guardText(row.guard.reasons)}` : text;
}

function PathList({ title, paths }: { title: string; paths: string[] }) {
  return (
    <details>
      <summary>
        {title}（{paths.length}）
      </summary>
      {paths.length ? (
        <ul>
          {paths.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      ) : (
        <p className="muted">{js.panel.noPaths}</p>
      )}
    </details>
  );
}

function Json({ value }: { value: unknown }) {
  return (
    <details>
      <summary>{js.panel.details}</summary>
      <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-all", maxHeight: 360, overflow: "auto" }}>{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}

function PanelView({ panel, onClose }: { panel: Panel; onClose: () => void }) {
  const title = js.panel[panel.type];
  let body;
  if (panel.type === "scan") {
    const r = panel.result;
    body = r.error === "integration_disabled" ? (
      <IntegrationOff reason={r.reason ?? ""} />
    ) : r.error ? (
      <p role="alert" className="error">
        {js.msg.scanError}: {r.error === "source_unavailable" ? js.msg.sourceUnavailable : r.error}
      </p>
    ) : (
      <>
        <p>
          {js.panel.counts}: {js.pendingText(r.counts?.added ?? 0, r.counts?.modified ?? 0, r.counts?.deleted ?? 0)}
        </p>
        <p className="muted">
          {js.panel.total}: {r.total_files ?? 0} ・ {js.panel.nameConflicts}: {r.name_conflicts ?? 0}
        </p>
        <Json value={r} />
      </>
    );
  } else if (panel.type === "plan") {
    const p = panel.plan;
    body = (
      <>
        <p>
          {js.panel.counts}: {js.pendingText(p.counts.added, p.counts.modified, p.counts.deleted)}
        </p>
        <p className="muted">
          {js.panel.appliedFiles}: {p.applied_files} ・ {js.panel.total}: {p.total_files}
        </p>
        <p className="muted">
          {js.msg.planSha}: <code>{p.plan_sha256}</code>
        </p>
        {p.guard.hold && (
          <p role="note" className="warn">
            {js.panel.holdWarning}（{guardText(p.guard.reasons)}）
          </p>
        )}
        <PathList title={js.panel.added} paths={p.added} />
        <PathList title={js.panel.modified} paths={p.modified} />
        <PathList title={js.panel.deleted} paths={p.deleted} />
        {Object.keys(p.conflicts).length > 0 && (
          <div>
            <p>{js.panel.conflicts}</p>
            <ul>
              {Object.entries(p.conflicts).map(([name, paths]) => (
                <li key={name}>
                  {name}: {paths.join(", ")}
                </li>
              ))}
            </ul>
          </div>
        )}
        {[p.added, p.modified, p.deleted].some((l) => l.length >= 200) && <p className="muted">{js.panel.moreNote}</p>}
      </>
    );
  } else if (panel.type === "changes") {
    body = panel.entries.length ? (
      <div style={{ overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              <th scope="col">{js.panel.colSeq}</th>
              <th scope="col">{js.panel.colAt}</th>
              <th scope="col">{js.panel.colKind}</th>
              <th scope="col">{js.panel.colSummary}</th>
            </tr>
          </thead>
          <tbody>
            {panel.entries.map((e) => (
              <tr key={`${e.seq}-${e.run_id}`}>
                <th scope="row">{e.seq}</th>
                <td>{dateTime(e.at)}</td>
                <td>{e.kind}</td>
                <td>
                  <code>{e.summary ? JSON.stringify(e.summary) : ""}</code>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    ) : (
      <p className="empty">{js.panel.noChanges}</p>
    );
  } else {
    const r = panel.result;
    body = (
      <>
        <p>{js.applyStatus[r.status] ?? r.status}</p>
        {r.reasons && r.reasons.length > 0 && <p className="warn">{guardText(r.reasons)}</p>}
        <Json value={r} />
      </>
    );
  }
  return (
    <section className="panel" aria-label={`${title}: ${panel.id}`}>
      <h2>
        {title}: {panel.id}
      </h2>
      {body}
      <button type="button" onClick={onClose}>
        {js.panel.close}
      </button>
    </section>
  );
}

function CreateForm({ onCreate, busy }: { onCreate: (body: CreateBody) => Promise<boolean>; busy: boolean }) {
  const [id, setId] = useState("");
  const [tenant, setTenant] = useState("default");
  const [kind, setKind] = useState("folder");
  const [path, setPath] = useState("");
  const [drive, setDrive] = useState("");
  const [folder, setFolder] = useState("");
  const [schedule, setSchedule] = useState("0");
  const [collection, setCollection] = useState("");
  const [problem, setProblem] = useState("");

  async function submit(e: FormEvent) {
    e.preventDefault();
    const text = schedule.trim();
    if (!id.trim()) return setProblem(js.form.needId);
    if (!tenant.trim()) return setProblem(js.form.needTenant);
    if (kind === "folder" && !path.trim()) return setProblem(js.form.needPath);
    if (kind !== "folder" && !drive.trim()) return setProblem(js.form.needDrive);
    if (text && !/^\d+$/.test(text)) return setProblem(js.form.badSchedule);
    setProblem("");
    const body: CreateBody = { source_id: id.trim(), tenant_id: tenant.trim(), kind, schedule_minutes: parseInt(text || "0", 10) || 0 };
    if (kind === "folder") body.path = path.trim();
    else body.options = { drive_id: drive.trim(), folder: folder.trim() };
    if (collection.trim()) body.collection = collection.trim();
    if (await onCreate(body)) {
      setId("");
      setPath("");
      setDrive("");
      setFolder("");
      setCollection("");
    }
  }

  return (
    <details className="panel">
      <summary>{js.form.summary}</summary>
      <form onSubmit={(e) => void submit(e)} noValidate>
        <div className="toolbar">
          <label>
            {js.form.id}
            <input value={id} onChange={(e) => setId(e.target.value)} placeholder="policies" maxLength={48} aria-describedby="f-id-help" />
          </label>
          <label>
            {js.form.tenant}
            <input value={tenant} onChange={(e) => setTenant(e.target.value)} maxLength={64} />
          </label>
          <label>
            {js.form.kind}
            <select value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="folder">{js.kinds.folder}</option>
              <option value="sharepoint">{js.kinds.sharepoint}</option>
            </select>
          </label>
        </div>
        <p id="f-id-help" className="muted">
          {js.form.idHelp}
        </p>
        <div className="toolbar">
          {kind === "folder" ? (
            <label>
              {js.form.path}
              <input value={path} onChange={(e) => setPath(e.target.value)} maxLength={1024} />
            </label>
          ) : (
            <>
              <label>
                {js.form.drive}
                <input value={drive} onChange={(e) => setDrive(e.target.value)} />
              </label>
              <label>
                {js.form.folder}
                <input value={folder} onChange={(e) => setFolder(e.target.value)} placeholder="/Shared Documents/Policies" />
              </label>
            </>
          )}
          <label>
            {js.form.schedule}
            <input value={schedule} onChange={(e) => setSchedule(e.target.value)} inputMode="numeric" />
          </label>
          <label>
            {js.form.collection}
            <input value={collection} onChange={(e) => setCollection(e.target.value)} maxLength={128} />
          </label>
        </div>
        {problem && (
          <p role="alert" className="error">
            {problem}
          </p>
        )}
        <button type="submit" disabled={busy}>
          {js.form.submit}
        </button>
      </form>
    </details>
  );
}

export function SourcesPage() {
  const list = useLoad(() => sourcesApi.list().then((r) => r.sources), [], js.loadFailed);
  const [message, setMessage] = useState<Message | null>(null);
  const [panel, setPanel] = useState<Panel | null>(null);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [busy, setBusy] = useState(false);
  const reload = list.reload;

  /** Run one action with the buttons disabled; `work` returns the success text, or throws. */
  async function act(prefix: string, work: () => Promise<string | null>) {
    setBusy(true);
    setMessage(null);
    try {
      const text = await work();
      if (text) setMessage({ ok: true, text });
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) setMessage({ ok: false, text: failure(e, prefix) });
    } finally {
      setBusy(false);
    }
  }

  const scan = (id: string) =>
    act(js.msg.scanFailed, async () => {
      const result = await sourcesApi.scan(id);
      setPanel({ type: "scan", id, result });
      reload();
      // An integration that is off is not a failure of the source: no alarm, the panel explains.
      if (result.error === "integration_disabled") return null;
      return result.error ? `${js.msg.scanError}: ${result.error === "source_unavailable" ? js.msg.sourceUnavailable : result.error}` : js.msg.scanned;
    });

  const plan = (id: string) =>
    act(js.msg.planFailed, async () => {
      const result = await sourcesApi.plan(id);
      setPanel({ type: "plan", id, plan: result });
      return `${js.msg.planSha}: ${result.plan_sha256}`;
    });

  const changes = (id: string) =>
    act(js.msg.changesFailed, async () => {
      const result = await sourcesApi.changes(id);
      setPanel({ type: "changes", id, entries: result.entries });
      return js.msg.changesShown;
    });

  const apply = (id: string) =>
    act(js.msg.applyFailed, async () => {
      const result = await sourcesApi.apply(id);
      setPanel({ type: "apply", id, result });
      if (result.status === "held") {
        setConfirm({ kind: "held", id, sha: result.plan_sha256 ?? "", reasons: result.reasons ?? [] });
        return null;
      }
      reload();
      return `${js.msg.applied}: ${js.applyStatus[result.status] ?? result.status}`;
    });

  const approveAndApply = (id: string, sha: string) =>
    act(js.msg.applyFailed, async () => {
      try {
        await sourcesApi.approve(id, sha);
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) throw e;
        setMessage({ ok: false, text: failure(e, js.msg.approveFailed) });
        return null;
      }
      const result = await sourcesApi.apply(id, sha);
      setPanel({ type: "apply", id, result });
      reload();
      return `${js.msg.approvedApplied}: ${js.applyStatus[result.status] ?? result.status}`;
    });

  const setEnabled = (id: string, enable: boolean) =>
    act(js.msg.switchFailed, async () => {
      await sourcesApi.setEnabled(id, enable);
      reload();
      return js.msg.switched;
    });

  const remove = (id: string) =>
    act(js.msg.removeFailed, async () => {
      await sourcesApi.remove(id);
      setPanel((p) => (p && p.id === id ? null : p));
      reload();
      return js.msg.removed;
    });

  const create = async (body: CreateBody): Promise<boolean> => {
    let ok = false;
    await act(js.msg.createFailed, async () => {
      await sourcesApi.create(body);
      ok = true;
      reload();
      return js.msg.created;
    });
    return ok;
  };

  function startApply(row: SourceRow) {
    if (row.pending && row.pending.deleted > 0) setConfirm({ kind: "apply", id: row.source_id, deleted: row.pending.deleted });
    else void apply(row.source_id);
  }

  function confirmed() {
    const c = confirm;
    setConfirm(null);
    if (!c) return;
    if (c.kind === "remove") void remove(c.id);
    else if (c.kind === "apply") void apply(c.id);
    else void approveAndApply(c.id, c.sha);
  }

  const rows = list.data;
  return (
    <section>
      <h1>{js.title}</h1>
      <p>
        {js.intro}
        <strong>{js.introStrong}</strong>
        {js.introEnd}
      </p>
      <div className="toolbar">
        <button type="button" disabled={busy || list.loading} onClick={reload}>
          {js.reload}
        </button>
        {rows && <span className="muted">{js.count(rows.length)}</span>}
      </div>
      <CreateForm onCreate={create} busy={busy} />
      <div aria-live="polite">
        {message && (
          <p role={message.ok ? "status" : "alert"} className={message.ok ? "notice" : "error"}>
            {message.text}
          </p>
        )}
      </div>
      {list.error ? (
        <ErrorState message={list.error} onRetry={reload} />
      ) : !rows ? (
        <Loading />
      ) : rows.length === 0 ? (
        <p className="empty">{js.empty}</p>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table>
            <thead>
              <tr>
                <th scope="col">{js.cols.source}</th>
                <th scope="col">{js.cols.tenant}</th>
                <th scope="col">{js.cols.kind}</th>
                <th scope="col">{js.cols.collection}</th>
                <th scope="col">{js.cols.schedule}</th>
                <th scope="col">{js.cols.pending}</th>
                <th scope="col">{js.cols.lastScan}</th>
                <th scope="col">{js.cols.lastApply}</th>
                <th scope="col">{js.cols.actions}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const id = row.source_id;
                const held = Boolean(row.guard?.hold);
                const button = (label: string, run: () => void) => (
                  <button type="button" disabled={busy} aria-label={js.actionFor(label, id)} onClick={run}>
                    {label}
                  </button>
                );
                return (
                  <tr key={id}>
                    <th scope="row" className={row.enabled ? "" : "muted"}>
                      {id}
                      {row.enabled ? "" : js.disabledSuffix}
                    </th>
                    <td>{row.tenant_id}</td>
                    <td>{js.kinds[row.kind] ?? row.kind}</td>
                    <td>{row.collection}</td>
                    <td>{row.schedule_minutes}</td>
                    <td className={held ? "warn" : ""}>{pendingText(row)}</td>
                    <td>{lastScan(row)}</td>
                    <td>{dateTime(row.state.last_apply_at)}</td>
                    <td>
                      <div className="toolbar">
                        {button(js.actions.scan, () => void scan(id))}
                        {button(js.actions.plan, () => void plan(id))}
                        {button(js.actions.apply, () => startApply(row))}
                        {button(js.actions.changes, () => void changes(id))}
                        {button(row.enabled ? js.actions.disable : js.actions.enable, () => void setEnabled(id, !row.enabled))}
                        {button(js.actions.remove, () => setConfirm({ kind: "remove", id }))}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {panel && <PanelView panel={panel} onClose={() => setPanel(null)} />}
      {confirm && (
        <ConfirmDialog
          title={confirm.kind === "remove" ? js.confirmRemove(confirm.id) : confirm.kind === "apply" ? js.confirmApply(confirm.deleted) : js.confirmHeld(guardText(confirm.reasons))}
          confirmLabel={confirm.kind === "remove" ? js.confirmRemoveLabel : confirm.kind === "held" ? js.confirmHeldLabel : js.actions.apply}
          busy={busy}
          onConfirm={confirmed}
          onCancel={() => {
            if (confirm.kind === "held") setMessage({ ok: true, text: js.msg.stillHeld });
            setConfirm(null);
          }}
        />
      )}
    </section>
  );
}
