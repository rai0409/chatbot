import { type FormEvent, useId, useState } from "react";
import { ASSIGNMENT_KINDS, limitsApi, type Assignment, type AssignmentChange, type Assignments, type AssignmentKind } from "../../api/limits";
import { ErrorState, Loading } from "../../components/Feedback";
import { ReasonDialog } from "../../components/ReasonDialog";
import { jaLimits as j } from "../../i18n/limits";
import { dateTime } from "../../lib/format";
import { useLoad } from "../../lib/useLoad";
import { failureText, isForbidden, Notice } from "./common";

const a = j.assign;
const FINGERPRINT_RE = /^[a-z0-9]{12}$/;

interface FormProps {
  data: Assignments;
  disabled: boolean;
  onDone: () => void;
  onForbidden: () => void;
}

function AssignForm({ data, disabled, onDone, onForbidden }: FormProps) {
  const f = a.form;
  const id = useId();
  const [kind, setKind] = useState<AssignmentKind>("global");
  const [target, setTarget] = useState("");
  const [tier, setTier] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  function changeKind(next: AssignmentKind) {
    setKind(next);
    setTarget("");
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const who = target.trim();
    if (kind !== "global" && !who) return setError(f.idRequired);
    if (kind === "user" && !FINGERPRINT_RE.test(who)) return setError(f.idUserInvalid);
    if (!tier) return setError(f.tierRequired);
    if (!reason.trim()) return setError(f.reasonRequired);
    const body: AssignmentChange = { kind, tier, reason: reason.trim() };
    if (kind !== "global") body.id = who;
    setBusy(true);
    setError("");
    try {
      await limitsApi.setAssignment(body);
      setTarget("");
      setTier("");
      setReason("");
      onDone();
    } catch (e) {
      if (isForbidden(e)) onForbidden();
      else setError(failureText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel" onSubmit={submit} noValidate aria-labelledby={`${id}-title`}>
      <h2 id={`${id}-title`}>{f.title}</h2>
      <fieldset disabled={disabled || busy} className="lm-inline">
        <label>
          {f.kind}
          <select value={kind} onChange={(e) => changeKind(e.target.value as AssignmentKind)}>
            {ASSIGNMENT_KINDS.map((k) => (
              <option key={k} value={k}>
                {a.kinds[k]}
              </option>
            ))}
          </select>
        </label>
        {kind === "tenant" && (
          <label>
            {f.idTenant}
            <input type="text" value={target} maxLength={64} autoComplete="off" onChange={(e) => setTarget(e.target.value)} />
          </label>
        )}
        {kind === "role" && (
          <label>
            {f.idRole}
            <select value={target} onChange={(e) => setTarget(e.target.value)}>
              <option value="">{f.choose}</option>
              {data.roles.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </label>
        )}
        {kind === "user" && (
          <label>
            {f.idUser}
            <input type="text" value={target} maxLength={12} autoComplete="off" spellCheck={false} aria-describedby={`${id}-fp`} onChange={(e) => setTarget(e.target.value)} />
            <span id={`${id}-fp`} className="muted">
              {f.idUserHelp}
            </span>
          </label>
        )}
        <label>
          {f.tier}
          <select value={tier} onChange={(e) => setTier(e.target.value)}>
            <option value="">{f.choose}</option>
            {data.tiers.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        <div className="lm-wide lm-field">
          <label htmlFor={`${id}-reason`}>{j.reasonLabel}</label>
          <textarea id={`${id}-reason`} value={reason} rows={2} maxLength={300} aria-describedby={`${id}-reason-help`} onChange={(e) => setReason(e.target.value)} />
          <span id={`${id}-reason-help`} className="muted">
            {j.reasonHelp}
          </span>
        </div>
      </fieldset>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div className="lm-actions">
        <button type="submit" disabled={disabled || busy}>
          {busy ? j.busy : f.save}
        </button>
      </div>
    </form>
  );
}

function List({ rows, disabled, onRemove }: { rows: Assignment[]; disabled: boolean; onRemove: (row: Assignment) => void }) {
  return (
    <section className="panel">
      <h2>{a.listTitle}</h2>
      {rows.length === 0 ? (
        <p className="empty">{a.listEmpty}</p>
      ) : (
        <div className="lm-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">{a.colKind}</th>
                <th scope="col">{a.colId}</th>
                <th scope="col">{a.colTier}</th>
                <th scope="col">{a.colBy}</th>
                <th scope="col">{a.colAt}</th>
                <th scope="col">{a.colReason}</th>
                <th scope="col">{a.colAction}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={`${row.kind}/${row.id ?? ""}`}>
                  <th scope="row">{a.kinds[row.kind] ?? row.kind}</th>
                  <td className={row.kind === "user" ? "lm-fp" : undefined}>{row.id ?? a.everyone}</td>
                  <td>
                    {row.tier ?? j.none}{" "}
                    {!row.defined && (
                      <span className="badge failing" title={a.undefinedHelp}>
                        {a.undefinedBadge}
                      </span>
                    )}
                    {!row.defined && <span className="muted"> {a.undefinedHelp}</span>}
                  </td>
                  <td>{row.by ?? j.none}</td>
                  <td>{dateTime(row.at)}</td>
                  <td>{row.reason ?? j.none}</td>
                  <td>
                    <button type="button" disabled={disabled} aria-label={a.removeFor(a.kinds[row.kind] ?? row.kind, row.id ?? a.everyone)} onClick={() => onRemove(row)}>
                      {a.remove}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function AssignmentsTab() {
  const state = useLoad(() => limitsApi.assignments(), [], a.failed);
  const [notice, setNotice] = useState("");
  const [readOnly, setReadOnly] = useState(false);
  const [removing, setRemoving] = useState<Assignment | null>(null);
  const [busy, setBusy] = useState(false);
  const [removeError, setRemoveError] = useState("");
  const { data } = state;

  async function remove(row: Assignment, reason: string) {
    const body: AssignmentChange = { kind: row.kind, tier: null, reason };
    if (row.id) body.id = row.id;
    setBusy(true);
    setRemoveError("");
    try {
      await limitsApi.setAssignment(body);
      setRemoving(null);
      setNotice(a.removed);
      state.reload();
    } catch (e) {
      if (isForbidden(e)) {
        setRemoving(null);
        setReadOnly(true);
      } else setRemoveError(failureText(e));
    } finally {
      setBusy(false);
    }
  }

  const saved = () => {
    setNotice(a.form.saved);
    state.reload();
  };
  const forbid = () => {
    setNotice("");
    setReadOnly(true);
  };

  if (state.error) return <ErrorState message={a.failed} onRetry={state.reload} />;
  if (data === null) return state.loading ? <Loading /> : null;

  const hasTiers = data.profiles_file_configured && data.tiers.length > 0;
  return (
    <div>
      <section className="panel">
        <h2>{a.explainTitle}</h2>
        <p>{a.explain}</p>
        <p>{a.precedence}</p>
      </section>
      <Notice text={notice} />
      {readOnly && (
        <p role="note" className="warn">
          {a.readOnly}
        </p>
      )}

      {hasTiers ? (
        <section className="panel">
          <h2>{a.profilesTitle}</h2>
          <h3 className="muted">{a.profilesLabel}</h3>
          {data.profiles.length === 0 ? (
            <p className="empty">{a.profilesNone}</p>
          ) : (
            <ul className="lm-tags">
              {data.profiles.map((p) => (
                <li key={p} className="badge">
                  {p}
                </li>
              ))}
            </ul>
          )}
          <h3 className="muted">{a.tiersLabel}</h3>
          <ul className="lm-tags">
            {data.tiers.map((t) => (
              <li key={t} className="badge">
                {t}
              </li>
            ))}
          </ul>
        </section>
      ) : (
        <section className="panel" role="note">
          <h2>{a.noProfilesTitle}</h2>
          <p>{a.noProfiles}</p>
        </section>
      )}

      {(hasTiers || data.assignments.length > 0) && <List rows={data.assignments} disabled={readOnly || busy} onRemove={(row) => setRemoving(row)} />}
      {hasTiers && <AssignForm data={data} disabled={readOnly} onDone={saved} onForbidden={forbid} />}

      {removing && (
        <>
          <ReasonDialog
            title={a.removeTitle}
            help={`${a.kinds[removing.kind] ?? removing.kind} ${removing.id ?? a.everyone} / ${removing.tier ?? ""}. ${j.reasonHelp}`}
            confirmLabel={a.removeConfirm}
            busy={busy}
            onConfirm={(text) => remove(removing, text)}
            onCancel={() => setRemoving(null)}
          />
          {removeError && (
            <p role="alert" className="error">
              {removeError}
            </p>
          )}
        </>
      )}
    </div>
  );
}
