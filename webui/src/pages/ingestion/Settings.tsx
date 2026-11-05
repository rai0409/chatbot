import { useState } from "react";
import { ApiError } from "../../api/client";
import { type IngestSetting, ingestionApi } from "../../api/ingestion";
import { ErrorState, Loading } from "../../components/Feedback";
import { ji } from "../../i18n/ingestion";
import { rememberActor, storedActor } from "../../lib/actor";
import { dateTime, number } from "../../lib/format";
import { useLoad } from "../../lib/useLoad";

const s = ji.settings;

function labelOf(setting: IngestSetting): [string, string] {
  return s.labels[setting.name] ?? [setting.name, ""];
}

function show(setting: IngestSetting, value: unknown): string {
  if (setting.kind === "bool") return value ? s.on : s.off;
  if (typeof value === "number") return `${number(value)}${setting.unit ? ` ${setting.unit}` : ""}`;
  return String(value);
}

type Dialog = { kind: "edit" | "reset"; setting: IngestSetting };

function failureText(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 409) return s.conflict;
    if (e.status === 403) return s.forbidden;
    if (e.status === 400 && e.detail) return s.failedWith(e.detail);
  }
  return s.failed;
}

/** The dialog of one change: the new value (not for a reset), the reason and the name of the person. */
function ChangeDialog({ dialog, busy, onSubmit, onCancel }: { dialog: Dialog; busy: boolean; onSubmit: (value: number | boolean | string | undefined, reason: string, actor: string) => void; onCancel: () => void }) {
  const { setting } = dialog;
  const [label] = labelOf(setting);
  const [raw, setRaw] = useState(String(setting.value));
  const [reason, setReason] = useState("");
  const [actor, setActor] = useState(storedActor);
  const [problem, setProblem] = useState("");

  function submit() {
    let value: number | boolean | string | undefined;
    if (dialog.kind === "edit") {
      if (setting.kind === "bool") value = raw === "true";
      else if (setting.kind === "choice") value = raw;
      else {
        const parsed = Number(raw);
        if (raw.trim() === "" || !Number.isFinite(parsed)) return setProblem(s.invalidNumber);
        if ((setting.minimum != null && parsed < setting.minimum) || (setting.maximum != null && parsed > setting.maximum)) return setProblem(s.outOfRange(setting.minimum, setting.maximum));
        value = parsed;
      }
    }
    if (!reason.trim() || !actor.trim()) return setProblem(s.needReason);
    setProblem("");
    rememberActor(actor.trim());
    onSubmit(value, reason.trim(), actor.trim());
  }

  return (
    <div role="dialog" aria-modal="true" aria-labelledby="ing-set-title" className="dialog">
      <h2 id="ing-set-title">{dialog.kind === "edit" ? s.dialogTitle(label) : s.resetTitle(label)}</h2>
      {dialog.kind === "reset" && <p>{s.resetBody}</p>}
      {dialog.kind === "edit" && (
        <label>
          {label}
          {setting.kind === "bool" ? (
            <select value={raw} onChange={(e) => setRaw(e.target.value)}>
              <option value="true">{s.on}</option>
              <option value="false">{s.off}</option>
            </select>
          ) : setting.kind === "choice" ? (
            <select value={raw} onChange={(e) => setRaw(e.target.value)}>
              {setting.choices.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          ) : (
            <input type="number" value={raw} step={setting.kind === "int" ? 1 : "any"} min={setting.minimum ?? undefined} max={setting.maximum ?? undefined} onChange={(e) => setRaw(e.target.value)} />
          )}
        </label>
      )}
      {dialog.kind === "edit" && setting.kind !== "bool" && setting.kind !== "choice" && <p className="muted">{s.range(setting.minimum, setting.maximum, setting.unit)}</p>}
      {setting.restart === "workers" && <p className="muted">{s.restartNote}</p>}
      <div>
        <label htmlFor="ing-set-reason">{s.reasonLabel}</label>
        <textarea id="ing-set-reason" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300} rows={2} />
      </div>
      <label>
        {s.actorLabel}
        <input value={actor} onChange={(e) => setActor(e.target.value)} maxLength={64} />
      </label>
      {problem && (
        <p role="alert" className="error">
          {problem}
        </p>
      )}
      <button type="button" disabled={busy} onClick={submit}>
        {dialog.kind === "edit" ? s.save : s.reset}
      </button>
      <button type="button" disabled={busy} onClick={onCancel}>
        {s.cancel}
      </button>
    </div>
  );
}

/** The ingestion parameters (limits, OCR, tables, headings, preprocessing, chunk sizes) and how to change them. */
export function SettingsSection() {
  const state = useLoad(ingestionApi.settings, [], s.loadFailed);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  const data = state.data;

  async function submit(value: number | boolean | string | undefined, reason: string, actor: string) {
    if (!dialog || !state.data) return;
    setBusy(true);
    try {
      await ingestionApi.changeSetting({
        name: dialog.setting.name,
        ...(dialog.kind === "reset" ? { reset: true } : { value }),
        reason,
        actor,
        expected_rev: state.data.rev,
      });
      setNotice({ ok: true, text: dialog.kind === "reset" ? s.resetDone : s.saved });
      setDialog(null);
      state.reload();
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) setNotice({ ok: false, text: failureText(e) });
      if (e instanceof ApiError && e.status === 409) {
        setDialog(null);
        state.reload();
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel" aria-labelledby="h-settings">
      <h2 id="h-settings">{s.heading}</h2>
      <p className="muted">{s.intro}</p>
      {notice && (
        <p role={notice.ok ? "status" : "alert"} className={notice.ok ? "notice" : "error"}>
          {notice.text}
        </p>
      )}
      {state.error ? (
        <ErrorState message={state.error} onRetry={state.reload} />
      ) : !data ? (
        <Loading />
      ) : (
        <>
          {!data.can_edit && <p className="warn">{s.readOnly}</p>}
          {data.groups.map((group) => (
            <div key={group} style={{ overflowX: "auto" }}>
              <h3>{s.groups[group] ?? group}</h3>
              <table>
                <thead>
                  <tr>
                    <th scope="col">{s.colName}</th>
                    <th scope="col">{s.colValue}</th>
                    <th scope="col">{s.colSource}</th>
                    <th scope="col">{s.colDefault}</th>
                    <th scope="col">{s.colAction}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.settings
                    .filter((x) => x.group === group)
                    .map((x) => {
                      const [label, help] = labelOf(x);
                      return (
                        <tr key={x.name}>
                          <th scope="row">
                            {label}
                            <br />
                            <span className="muted">{help}</span>
                            {x.kind !== "bool" && x.kind !== "choice" && (
                              <>
                                <br />
                                <span className="muted">{s.range(x.minimum, x.maximum, x.unit)}</span>
                              </>
                            )}
                            {x.source === "override" && x.by && (
                              <>
                                <br />
                                <span className="muted">{s.changedBy(x.by, x.reason ?? "")}</span>
                              </>
                            )}
                          </th>
                          <td>{show(x, x.value)}</td>
                          <td>{s.sources[x.source] ?? x.source}</td>
                          <td>{show(x, x.default)}</td>
                          <td>
                            {data.can_edit && (
                              <>
                                <button type="button" aria-label={`${s.edit}: ${label}`} disabled={busy} onClick={() => setDialog({ kind: "edit", setting: x })}>
                                  {s.edit}
                                </button>{" "}
                                {x.source === "override" && (
                                  <button type="button" aria-label={`${s.reset}: ${label}`} disabled={busy} onClick={() => setDialog({ kind: "reset", setting: x })}>
                                    {s.reset}
                                  </button>
                                )}
                              </>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                </tbody>
              </table>
            </div>
          ))}
          <h3>{s.historyHeading}</h3>
          {data.history.length === 0 ? (
            <p className="empty">{s.historyEmpty}</p>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table>
                <thead>
                  <tr>
                    <th scope="col">{s.colWhen}</th>
                    <th scope="col">{s.colName}</th>
                    <th scope="col">{s.colBy}</th>
                    <th scope="col">{s.colChange}</th>
                    <th scope="col">{s.colReason}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.history.map((h) => (
                    <tr key={h.rev}>
                      <td>{dateTime(h.at)}</td>
                      <td>{(s.labels[h.name] ?? [h.name])[0]}</td>
                      <td>{h.by}</td>
                      <td>
                        {String(h.before ?? "—")} → {String(h.after ?? "—")}
                      </td>
                      <td>{h.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
      {dialog && <ChangeDialog dialog={dialog} busy={busy} onSubmit={(v, r, a) => void submit(v, r, a)} onCancel={() => setDialog(null)} />}
    </section>
  );
}
