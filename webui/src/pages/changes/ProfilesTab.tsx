import { useState } from "react";
import { type ProfileDiff, changesApi } from "../../api/changes";
import { ErrorState, Loading } from "../../components/Feedback";
import { ReasonDialog } from "../../components/ReasonDialog";
import { jaChanges as c } from "../../i18n/changes";
import { dateTime } from "../../lib/format";
import { useLoad } from "../../lib/useLoad";
import { ActorField, ReadOnlyNote, failureText, isForbidden, useActor, useReadOnly } from "./common";

const SOURCE = "source";

export function ProfilesTab() {
  const [selected, setSelected] = useState("");
  const { data, error, loading, reload } = useLoad(() => changesApi.profiles(), [], c.loadFailed);
  if (selected) return <ProfileDetail id={selected} onBack={() => { setSelected(""); reload(); }} />;
  if (error) return <ErrorState message={error} onRetry={reload} />;
  if (!data || loading) return <Loading />;
  return (
    <section>
      <p className="muted">{c.profiles.intro}</p>
      {data.profiles.length === 0 ? (
        <p className="empty">{c.profiles.empty}</p>
      ) : (
        <div className="ch-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">{c.profiles.colProfile}</th>
                <th scope="col">{c.profiles.colVersions}</th>
                <th scope="col">{c.profiles.colActive}</th>
                <th scope="col">{c.profiles.colAction}</th>
              </tr>
            </thead>
            <tbody>
              {data.profiles.map((p) => (
                <tr key={p.profile_id}>
                  <th scope="row">{p.profile_id}</th>
                  <td>{p.versions}</td>
                  <td>{p.active ? <code>{p.active}</code> : <span className="muted">{c.profiles.notPinned}</span>}</td>
                  <td>
                    <button type="button" onClick={() => setSelected(p.profile_id)} aria-label={`${c.profiles.open}: ${p.profile_id}`}>
                      {c.profiles.open}
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

type Pending = { kind: "activate"; version: string } | { kind: "rollback" };

function ProfileDetail({ id, onBack }: { id: string; onBack: () => void }) {
  const { data, error, loading, reload } = useLoad(() => changesApi.versions(id), [id], c.loadFailed);
  const { readOnly, markReadOnly } = useReadOnly();
  const [actor, setActor] = useActor();
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [failed, setFailed] = useState("");
  const [left, setLeft] = useState("");
  const [right, setRight] = useState("");
  const [diff, setDiff] = useState<ProfileDiff | null>(null);
  const [diffError, setDiffError] = useState("");

  async function confirm(reason: string) {
    if (!pending) return;
    setBusy(true);
    setNotice("");
    setFailed("");
    try {
      if (pending.kind === "activate") await changesApi.activate(id, { version: pending.version, actor: actor.trim(), reason });
      else await changesApi.rollback(id, { actor: actor.trim(), reason });
      setNotice(c.profiles.changed);
      setPending(null);
      reload();
    } catch (e) {
      if (isForbidden(e)) markReadOnly();
      setFailed(failureText(e));
      setPending(null);
    } finally {
      setBusy(false);
    }
  }

  function ask(next: Pending) {
    if (!actor.trim()) {
      setFailed(c.actor.needed);
      return;
    }
    setFailed("");
    setPending(next);
  }

  async function compare() {
    setDiff(null);
    setDiffError("");
    try {
      setDiff(await changesApi.diff(id, left, right));
    } catch (e) {
      setDiffError(failureText(e));
    }
  }

  if (error) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return <Loading />;

  const choices = [...data.versions.map((v) => ({ value: v.version, label: v.version })), { value: SOURCE, label: c.profiles.source }];
  return (
    <section aria-busy={loading}>
      <p>
        <button type="button" onClick={onBack}>
          {c.back}
        </button>
      </p>
      <h2>{id}</h2>
      <ReadOnlyNote />
      <p className="warn">{c.profiles.effect}</p>
      {notice && (
        <p role="status" className="notice">
          {notice}
        </p>
      )}
      {failed && (
        <p role="alert" className="error">
          {failed}
        </p>
      )}
      <ActorField actor={actor} onChange={setActor} />

      <h3>{c.profiles.versions}</h3>
      <p>
        <button type="button" disabled={busy || readOnly || data.history.length === 0} onClick={() => ask({ kind: "rollback" })}>
          {c.profiles.rollback}
        </button>
      </p>
      {data.versions.length === 0 ? (
        <p className="empty">{c.profiles.noVersions}</p>
      ) : (
        <div className="ch-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">{c.profiles.colVersion}</th>
                <th scope="col">{c.profiles.colCreated}</th>
                <th scope="col">{c.profiles.colBy}</th>
                <th scope="col">{c.profiles.colFiles}</th>
                <th scope="col">{c.profiles.colNote}</th>
                <th scope="col">{c.profiles.colAction}</th>
              </tr>
            </thead>
            <tbody>
              {data.versions.map((v) => (
                <tr key={v.version}>
                  <th scope="row">
                    <code>{v.version}</code> {v.version === data.active && <span className="badge ok">{c.profiles.activeMark}</span>}
                  </th>
                  {v.error ? (
                    <td colSpan={4}>
                      <span className="badge failing">{c.profiles.brokenVersion}</span> {v.error}
                    </td>
                  ) : (
                    <>
                      <td>{dateTime(v.created_at)}</td>
                      <td>{v.created_by || "—"}</td>
                      <td>{(v.files ?? []).join(", ") || "—"}</td>
                      <td>{v.note || "—"}</td>
                    </>
                  )}
                  {v.error ? null : (
                    <td>
                      {v.version === data.active ? (
                        <span className="muted">{c.profiles.active}</span>
                      ) : (
                        <button type="button" disabled={busy || readOnly} onClick={() => ask({ kind: "activate", version: v.version })}>
                          {c.profiles.activate}
                        </button>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h3>{c.profiles.history}</h3>
      {data.history.length === 0 ? (
        <p className="empty">{c.profiles.historyEmpty}</p>
      ) : (
        <div className="ch-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">{c.profiles.colVersion}</th>
                <th scope="col">{c.profiles.colActivated}</th>
                <th scope="col">{c.profiles.colActivatedBy}</th>
                <th scope="col">{c.profiles.colDeactivated}</th>
              </tr>
            </thead>
            <tbody>
              {[...data.history].reverse().map((h) => (
                <tr key={`${h.version}-${h.activated_at}`}>
                  <th scope="row">
                    <code>{h.version}</code>
                  </th>
                  <td>{dateTime(h.activated_at)}</td>
                  <td>{h.activated_by || "—"}</td>
                  <td>{dateTime(h.deactivated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h3>{c.profiles.compare}</h3>
      <div className="toolbar">
        <label>
          {c.profiles.left}
          <select value={left} onChange={(e) => setLeft(e.target.value)}>
            <option value="">{c.moves.categoryPick}</option>
            {choices.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          {c.profiles.right}
          <select value={right} onChange={(e) => setRight(e.target.value)}>
            <option value="">{c.moves.categoryPick}</option>
            {choices.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <button type="button" disabled={!left || !right} onClick={compare}>
          {c.profiles.run}
        </button>
      </div>
      {!left || !right ? <p className="muted">{c.profiles.pickTwo}</p> : null}
      {diffError && (
        <p role="alert" className="error">
          {diffError}
        </p>
      )}
      {diff && (
        <div className="panel" aria-live="polite">
          <p>
            <strong>{diff.identical ? c.profiles.identical : c.profiles.differs}</strong>
          </p>
          <table>
            <thead>
              <tr>
                <th scope="col">{c.profiles.colFile}</th>
                <th scope="col">{c.profiles.colResult}</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(diff.files).map(([name, result]) => (
                <tr key={name}>
                  <th scope="row">{name}</th>
                  <td>
                    <span className={`badge ${result === "same" ? "disabled" : "not_configured"}`}>{c.profiles.diff[result] ?? result}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {pending && (
        <ReasonDialog
          title={pending.kind === "activate" ? c.profiles.activateTitle(pending.version) : c.profiles.rollbackTitle}
          help={c.profiles.effect}
          busy={busy}
          onConfirm={confirm}
          onCancel={() => setPending(null)}
        />
      )}
    </section>
  );
}
