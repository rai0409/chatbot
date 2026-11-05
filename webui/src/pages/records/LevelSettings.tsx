import { useState } from "react";
import { LEVELS, recordsApi, type LevelOverview, type RecordLevel } from "../../api/records";
import { ErrorState, Loading } from "../../components/Feedback";
import { ReasonDialog } from "../../components/ReasonDialog";
import { jaRecords } from "../../i18n/records";
import { useLoad } from "../../lib/useLoad";
import { failureText } from "./errors";

const r = jaRecords.level;
const rank = (level: RecordLevel) => LEVELS.indexOf(level);

type Target = { tenant: string | null; askTenant: boolean; current: RecordLevel | null };
type Removal = { tenant: string };

export function LevelSettings() {
  const { data, error, loading, reload } = useLoad(() => recordsApi.level(), [], jaRecords.common.loadFailed);
  const [overview, setOverview] = useState<LevelOverview | null>(null);
  const [target, setTarget] = useState<Target | null>(null);
  const [removal, setRemoval] = useState<Removal | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [problem, setProblem] = useState("");

  const shown = overview ?? data;
  if (error && !shown) return <ErrorState message={error} onRetry={reload} />;
  if (!shown) return loading ? <Loading /> : null;
  const current = shown;

  async function send(level: RecordLevel | null, reason: string, tenant: string | null) {
    setBusy(true);
    setNotice("");
    setProblem("");
    try {
      const next = await recordsApi.setLevel({ level, reason, ...(tenant ? { tenant_id: tenant } : {}) });
      setOverview(next);
      setNotice(r.changed);
      setTarget(null);
      setRemoval(null);
    } catch (e) {
      setProblem(failureText(e, r.badRequest, tenant ? r.forbiddenTenant : r.forbiddenGlobal));
      setTarget(null);
      setRemoval(null);
    } finally {
      setBusy(false);
    }
  }

  const tenants = Object.entries(current.tenants);
  return (
    <div>
      <p>{r.intro}</p>
      <div className="rec-levels" role="list">
        {LEVELS.map((level) => {
          const above = rank(level) > rank(current.ceiling);
          return (
            <div key={level} role="listitem" className={`panel${above ? " off-limit" : ""}`}>
              <h3 style={{ marginTop: 0 }}>
                {r.names[level]}
                {level === current.ceiling && <span className="badge"> {r.reachedCeiling}</span>}
              </h3>
              <p>{r.keeps[level]}</p>
              {above && <p className="warn">{r.aboveCeiling}</p>}
            </div>
          );
        })}
      </div>

      <div className="kpis">
        <div className="kpi">
          <span>{r.default}</span>
          <b>{r.names[current.default]}</b>
        </div>
        <div className="kpi">
          <span>{r.ceiling}</span>
          <b>{r.names[current.ceiling]}</b>
        </div>
        <div className="kpi">
          <span>{r.global}</span>
          <b>{r.names[current.global]}</b>
        </div>
        <div className="kpi">
          <span>{r.effective}</span>
          <b>{r.names[current.effective]}</b>
        </div>
      </div>
      <p className="muted">{r.ceilingHelp}</p>

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

      <p>
        <button type="button" disabled={busy} onClick={() => setTarget({ tenant: null, askTenant: false, current: current.global })}>
          {r.changeGlobal}
        </button>{" "}
        <button type="button" disabled={busy} onClick={() => setTarget({ tenant: null, askTenant: true, current: null })}>
          {r.addTenant}
        </button>
      </p>

      <h2>{r.tenants}</h2>
      {tenants.length === 0 ? (
        <p className="empty">{r.noTenants}</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th scope="col">{r.colTenant}</th>
              <th scope="col">{r.colAsked}</th>
              <th scope="col">{r.colEffective}</th>
              <th scope="col">{r.colAction}</th>
            </tr>
          </thead>
          <tbody>
            {tenants.map(([tenant, level]) => (
              <tr key={tenant}>
                <th scope="row">{tenant}</th>
                <td>{r.names[level.requested]}</td>
                <td>{r.names[level.effective]}</td>
                <td>
                  <button
                    type="button"
                    disabled={busy}
                    aria-label={r.changeTenant(tenant)}
                    onClick={() => setTarget({ tenant, askTenant: false, current: level.requested })}
                  >
                    {r.change}
                  </button>{" "}
                  <button type="button" disabled={busy} aria-label={r.removeTitle(tenant)} onClick={() => setRemoval({ tenant })}>
                    {r.follow}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {target && (
        <LevelDialog
          target={target}
          ceiling={current.ceiling}
          busy={busy}
          onCancel={() => setTarget(null)}
          onConfirm={(level, reason, tenant) => void send(level, reason, tenant)}
        />
      )}
      {removal && (
        <ReasonDialog
          title={r.removeTitle(removal.tenant)}
          label={r.reason}
          help={r.reasonHelp}
          busy={busy}
          onCancel={() => setRemoval(null)}
          onConfirm={(reason) => void send(null, reason, removal.tenant)}
        />
      )}
    </div>
  );
}

function LevelDialog({
  target,
  ceiling,
  busy,
  onCancel,
  onConfirm,
}: {
  target: Target;
  ceiling: RecordLevel;
  busy: boolean;
  onCancel: () => void;
  onConfirm: (level: RecordLevel, reason: string, tenant: string | null) => void;
}) {
  const [level, setLevel] = useState<RecordLevel | "">(target.current ?? "");
  const [tenant, setTenant] = useState(target.tenant ?? "");
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState("");
  const title = target.askTenant ? r.addTenant : target.tenant ? r.changeTenant(target.tenant) : r.changeGlobal;

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (target.askTenant && !tenant.trim()) return setProblem(r.needTenant);
    if (!level || !reason.trim()) return;
    onConfirm(level, reason.trim(), target.askTenant ? tenant.trim() : target.tenant);
  }

  return (
    <form role="dialog" aria-modal="true" aria-labelledby="level-dialog-title" className="dialog" onSubmit={submit}>
      <h2 id="level-dialog-title">{title}</h2>
      {target.askTenant && (
        <>
          <label className="block" style={{ display: "flex", flexDirection: "column" }}>
            {r.tenantId}
            <input value={tenant} maxLength={64} onChange={(e) => setTenant(e.target.value)} />
          </label>
          <p className="muted">{r.tenantIdHelp}</p>
        </>
      )}
      <fieldset>
        <legend>{r.choose}</legend>
        {LEVELS.map((item) => {
          const above = rank(item) > rank(ceiling);
          return (
            <div key={item}>
              <label>
                <input type="radio" name="level" value={item} checked={level === item} disabled={above} onChange={() => setLevel(item)} /> {r.names[item]}
                {above ? `（${r.aboveCeiling}）` : ""}
              </label>
            </div>
          );
        })}
      </fieldset>
      <label style={{ display: "block" }}>
        {r.reason}
        <textarea value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300} rows={3} />
      </label>
      <p className="muted">{r.reasonHelp}</p>
      {problem && (
        <p role="alert" className="error">
          {problem}
        </p>
      )}
      <button type="submit" disabled={busy || !level || !reason.trim()}>
        {jaRecords.common.confirm}
      </button>{" "}
      <button type="button" onClick={onCancel}>
        {jaRecords.common.cancel}
      </button>
    </form>
  );
}
