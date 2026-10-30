import { useState } from "react";
import { contractsApi, type ContractDetail, type ContractField, type Deadline } from "../../api/contracts";
import { ApiError } from "../../api/client";
import { ErrorState, Loading } from "../../components/Feedback";
import { dateTime } from "../../lib/format";
import { jaContracts as c } from "../../i18n/contracts";
import { useLoad } from "../../lib/useLoad";
import { IntegrationOff, daysText, displayValue, failureText, integrationOffReason, kindText, type Notice } from "./helpers";

interface Props {
  tenant: string;
  contractId: string;
  onNotice: (n: Notice) => void;
  onChanged: () => void;
  onClose: () => void;
}

export function DetailPanel({ tenant, contractId, onNotice, onChanged, onClose }: Props) {
  const { data, error, loading, reload } = useLoad(() => contractsApi.detail(tenant, contractId), [tenant, contractId], c.detailFailed);
  const [busy, setBusy] = useState(false);
  const [draftFor, setDraftFor] = useState<Deadline | null>(null);
  const [off, setOff] = useState<string | null>(null);

  async function change(run: () => Promise<unknown>, failure: string) {
    setBusy(true);
    try {
      await run();
      onNotice({ kind: "ok", text: c.saved });
      reload();
      onChanged();
    } catch (e) {
      onNotice({ kind: "error", text: failureText(failure, e) });
    } finally {
      setBusy(false);
    }
  }

  async function createDraft(deadline: Deadline, mailbox: string) {
    setBusy(true);
    setOff(null);
    try {
      await contractsApi.draft(tenant, contractId, deadline.kind, mailbox);
      setDraftFor(null);
      onNotice({ kind: "ok", text: c.draftDone });
      reload();
    } catch (e) {
      const reason = e instanceof ApiError && e.status === 409 ? integrationOffReason(e.detail) : null;
      setDraftFor(null);
      if (reason !== null) setOff(reason);
      else onNotice({ kind: "error", text: failureText(c.draftFailed, e) });
    } finally {
      setBusy(false);
    }
  }

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;
  return (
    <section className="panel" aria-label={data.contract_id}>
      <Body
        data={data}
        busy={busy}
        onClose={onClose}
        onConfirm={(name) => void change(() => contractsApi.confirmField(tenant, contractId, name), c.saveFailed)}
        onSave={(name, value) => void change(() => contractsApi.setField(tenant, contractId, name, value), c.saveFailed)}
        onDraft={setDraftFor}
      />
      {off !== null && <IntegrationOff text={c.integrationOffDraft} reason={off} />}
      {draftFor && <DraftDialog deadline={draftFor} busy={busy} onCreate={(m) => void createDraft(draftFor, m)} onCancel={() => setDraftFor(null)} />}
    </section>
  );
}

function Body({ data, busy, onClose, onConfirm, onSave, onDraft }: {
  data: ContractDetail;
  busy: boolean;
  onClose: () => void;
  onConfirm: (name: string) => void;
  onSave: (name: string, value: string) => void;
  onDraft: (d: Deadline) => void;
}) {
  const unconfirmed = Object.values(data.fields).filter((f) => f.value !== null && f.state === "extracted").length;
  return (
    <>
      <div className="toolbar">
        <h2>{c.detailTitle(data.contract_id, c.status[data.status] ?? data.status)}</h2>
        <button type="button" onClick={onClose}>
          {c.close}
        </button>
      </div>
      {data.source_doc && (
        <p className="muted">
          {c.sourceDoc}: {data.source_doc}
        </p>
      )}
      {unconfirmed > 0 ? (
        <p role="note" className="panel contracts-unconfirmed-banner">
          <b>{c.unconfirmedWarn(unconfirmed)}</b>
        </p>
      ) : (
        <p className="contracts-ok">{c.allConfirmed}</p>
      )}
      <h3>{c.fieldsTitle}</h3>
      <p className="muted">{c.extractedHelp}</p>
      <div className="contracts-scroll">
        <table>
          <thead>
            <tr>
              <th scope="col">{c.colField}</th>
              <th scope="col">{c.colValue}</th>
              <th scope="col">{c.colState}</th>
              <th scope="col">{c.colEvidence}</th>
              <th scope="col">{c.colOps}</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(data.fields).map(([name, field]) => (
              <FieldRow key={`${name}|${field.value}|${field.state}`} name={name} field={field} required={data.required.includes(name)} busy={busy} onConfirm={onConfirm} onSave={onSave} />
            ))}
          </tbody>
        </table>
      </div>
      <h3>{c.deadlinesTitle}</h3>
      <div className="contracts-scroll">
        <table>
          <tbody>
            {data.deadlines.map((d) => (
              <tr key={d.kind}>
                <th scope="row">{kindText(d.kind, d.label)}</th>
                <td>{d.date}</td>
                <td>{daysText(d.days_left)}</td>
                <td className={d.verified ? "contracts-ok" : "contracts-attn"}>{d.verified ? `${c.stateMark.confirmed} ${c.verified}` : `${c.stateMark.extracted} ${c.unverifiedBasis}`}</td>
                <td>
                  <button type="button" disabled={busy} aria-label={c.draftButtonLabel(kindText(d.kind, d.label))} onClick={() => onDraft(d)}>
                    {c.draftButton}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h3>{c.historyTitle}</h3>
      {data.history.length === 0 ? (
        <p className="empty">{c.historyEmpty}</p>
      ) : (
        <div className="contracts-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">{c.colAt}</th>
                <th scope="col">{c.colBy}</th>
                <th scope="col">{c.colAction}</th>
              </tr>
            </thead>
            <tbody>
              {data.history.map((h, i) => (
                <tr key={i}>
                  <td>{dateTime(h.at)}</td>
                  <td>{h.by}</td>
                  <td>
                    {c.actions[h.action] ?? h.action}
                    {h.field ? ` ${c.fieldNames[h.field] ?? h.field}` : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function FieldRow({ name, field, required, busy, onConfirm, onSave }: {
  name: string;
  field: ContractField;
  required: boolean;
  busy: boolean;
  onConfirm: (name: string) => void;
  onSave: (name: string, value: string) => void;
}) {
  const [value, setValue] = useState(displayValue(field.value));
  const label = c.fieldNames[name] ?? name;
  const ev = field.evidence;
  return (
    <tr>
      <th scope="row">
        {label}
        {required ? <span className="muted"> {c.required}</span> : null}
      </th>
      <td>
        <input className="contracts-field-input" aria-label={c.valueLabel(label)} value={value} onChange={(e) => setValue(e.target.value)} maxLength={500} />
      </td>
      <td>
        <span className={`contracts-state st-${field.state}`}>
          <span aria-hidden="true">{c.stateMark[field.state] ?? ""} </span>
          {c.state[field.state] ?? field.state}
        </span>
        {field.confidence !== null && field.state === "extracted" && <div className="contracts-evidence">{c.confidence(field.confidence)}</div>}
      </td>
      <td className="contracts-evidence">{ev ? (ev.page ? c.page(ev.page) : "") + (ev.text || "") : ""}</td>
      <td>
        <button type="button" disabled={busy || field.value === null} aria-label={c.confirmLabel(label)} onClick={() => onConfirm(name)}>
          {c.confirm}
        </button>{" "}
        <button type="button" disabled={busy} aria-label={c.saveLabel(label)} onClick={() => onSave(name, value)}>
          {c.save}
        </button>
      </td>
    </tr>
  );
}

function DraftDialog({ deadline, busy, onCreate, onCancel }: { deadline: Deadline; busy: boolean; onCreate: (mailbox: string) => void; onCancel: () => void }) {
  const [mailbox, setMailbox] = useState("");
  const ok = mailbox.trim().length > 0;
  return (
    <div role="dialog" aria-modal="true" aria-labelledby="draft-title" className="dialog">
      <h2 id="draft-title">{c.draftTitle}</h2>
      <p>{kindText(deadline.kind, deadline.label)}</p>
      <label>
        {c.draftMailbox}
        <input type="email" value={mailbox} onChange={(e) => setMailbox(e.target.value)} maxLength={320} autoComplete="off" autoFocus />
      </label>
      <p className="muted">{c.draftHelp}</p>
      <button type="button" disabled={busy || !ok} onClick={() => onCreate(mailbox.trim())}>
        {c.draftCreate}
      </button>{" "}
      <button type="button" onClick={onCancel}>
        {c.cancel}
      </button>
    </div>
  );
}
