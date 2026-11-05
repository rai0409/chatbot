import { useState } from "react";
import { contractsApi, type NotifyResult } from "../api/contracts";
import { ErrorState, Loading } from "../components/Feedback";
import { jaContracts as c } from "../i18n/contracts";
import { useLoad } from "../lib/useLoad";
import { DetailPanel } from "./contracts/DetailPanel";
import { ImportPanel } from "./contracts/ImportPanel";
import { IntegrationOff, daysText, failureText, integrationOffReason, kindText, type Notice } from "./contracts/helpers";
import "./contracts/contracts.css";

export function ContractsPage() {
  const [tenantInput, setTenantInput] = useState("default");
  const [tenant, setTenant] = useState("default");
  const [selected, setSelected] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmSend, setConfirmSend] = useState(false);
  const [notify, setNotify] = useState<NotifyResult | null>(null);

  const list = useLoad(() => contractsApi.list(tenant), [tenant], c.listFailed);
  const upcoming = useLoad(() => contractsApi.upcoming(tenant), [tenant], c.upcomingFailed);

  function reloadAll() {
    list.reload();
    upcoming.reload();
  }

  function applyTenant() {
    const next = tenantInput.trim() || "default";
    setNotice(null);
    setNotify(null);
    if (next === tenant) return reloadAll();
    setSelected(null);
    setTenant(next);
  }

  async function runNotify(dryRun: boolean) {
    setConfirmSend(false);
    setBusy(true);
    setNotice(null);
    try {
      const res = await contractsApi.notify(tenant, dryRun);
      setNotify(res);
      const text = dryRun ? c.notifyPlan(res.due, res.sent, res.failed) : c.notifySentText(res.due, res.sent, res.failed);
      setNotice({ kind: res.failed > 0 && !dryRun ? "error" : "ok", text });
    } catch (e) {
      setNotify(null);
      setNotice({ kind: "error", text: failureText(c.notifyFailed, e) });
    } finally {
      setBusy(false);
    }
  }

  const offReason = notify?.results.map((r) => integrationOffReason(r.reason)).find((r) => r !== null) ?? null;

  return (
    <div>
      <h1>{c.title}</h1>
      <p>{c.intro}</p>
      <div className="toolbar">
        <label>
          {c.tenant}
          <input value={tenantInput} onChange={(e) => setTenantInput(e.target.value)} maxLength={64} size={14} autoComplete="off" />
        </label>
        <button type="button" onClick={applyTenant}>
          {c.reload}
        </button>
        <button type="button" disabled={busy} onClick={() => void runNotify(true)}>
          {c.dryRun}
        </button>
        <button type="button" disabled={busy} onClick={() => setConfirmSend(true)}>
          {c.sendNow}
        </button>
      </div>
      {notice && (
        <p role={notice.kind === "error" ? "alert" : "status"} className={notice.kind === "error" ? "error" : "notice"}>
          {notice.text}
        </p>
      )}
      {!notice && list.data && <p className="muted">{c.count(list.data.contracts.length)}</p>}
      {offReason !== null && <IntegrationOff text={c.integrationOffNotify} reason={offReason} />}
      {notify && (
        <section className="panel" aria-label={c.notifyResults}>
          <h2>{c.notifyResults}</h2>
          {notify.results.length === 0 ? (
            <p className="empty">{c.notifyEmpty}</p>
          ) : (
            <div className="contracts-scroll">
              <table>
                <thead>
                  <tr>
                    <th scope="col">{c.colDate}</th>
                    <th scope="col">{c.colContract}</th>
                    <th scope="col">{c.colKind}</th>
                    <th scope="col">{c.colConfirm}</th>
                    <th scope="col">{c.colResult}</th>
                    <th scope="col">{c.colReason}</th>
                  </tr>
                </thead>
                <tbody>
                  {notify.results.map((r) => (
                    <tr key={r.key}>
                      <td>{r.date}</td>
                      <th scope="row">{r.contract_id}</th>
                      <td>{kindText(r.kind, "")}</td>
                      <td className={r.verified ? "contracts-ok" : "contracts-attn"}>{r.verified ? c.verified : c.unverified}</td>
                      <td>{c.rowStatus[r.status] ?? r.status}</td>
                      <td>{r.reason ? (integrationOffReason(r.reason) !== null ? c.integrationOffNotify : r.reason) : ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
      {confirmSend && (
        <div role="dialog" aria-modal="true" aria-labelledby="notify-title" className="dialog">
          <h2 id="notify-title">{c.notifyConfirmTitle}</h2>
          <p>{c.notifyConfirmBody}</p>
          <button type="button" disabled={busy} onClick={() => void runNotify(false)}>
            {c.notifySend}
          </button>{" "}
          <button type="button" onClick={() => setConfirmSend(false)}>
            {c.cancel}
          </button>
        </div>
      )}
      <ImportPanel
        tenant={tenant}
        onNotice={setNotice}
        onImported={reloadAll}
      />
      <div className="contracts-layout">
        <div>
          <h2>{c.listTitle}</h2>
          {list.loading && !list.data && <Loading />}
          {list.error && !list.data && <ErrorState message={list.error} onRetry={list.reload} />}
          {list.data && list.data.contracts.length === 0 && <p className="empty">{c.listEmpty}</p>}
          {list.data && list.data.contracts.length > 0 && (
            <div className="contracts-scroll">
              <table>
                <thead>
                  <tr>
                    <th scope="col">{c.colContract}</th>
                    <th scope="col">{c.colStatus}</th>
                    <th scope="col">{c.colNearest}</th>
                    <th scope="col">{c.colUnconfirmed}</th>
                  </tr>
                </thead>
                <tbody>
                  {list.data.contracts.map((ct) => {
                    const name = `${ct.title || ct.contract_id} / ${ct.counterparty || c.noCounterparty}`;
                    const d = ct.nearest_deadline;
                    return (
                      <tr key={ct.contract_id} aria-selected={selected === ct.contract_id}>
                        <th scope="row">
                          <button type="button" aria-label={c.openLabel(name)} onClick={() => setSelected(ct.contract_id)}>
                            {name}
                          </button>
                        </th>
                        <td className={ct.status === "needs_review" ? "contracts-attn" : ""}>{c.status[ct.status] ?? ct.status}</td>
                        <td>{d ? `${d.date} ${kindText(d.kind, d.label)}${d.verified ? "" : c.unverifiedMark}` : c.none}</td>
                        <td className={ct.unconfirmed ? "contracts-attn" : ""}>{ct.unconfirmed}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <h2>{c.upcomingTitle}</h2>
          {upcoming.loading && !upcoming.data && <Loading />}
          {upcoming.error && !upcoming.data && <ErrorState message={upcoming.error} onRetry={upcoming.reload} />}
          {upcoming.data && upcoming.data.deadlines.length === 0 && <p className="empty">{c.upcomingEmpty}</p>}
          {upcoming.data && upcoming.data.deadlines.length > 0 && (
            <div className="contracts-scroll">
              <table>
                <thead>
                  <tr>
                    <th scope="col">{c.colDate}</th>
                    <th scope="col">{c.colContract}</th>
                    <th scope="col">{c.colKind}</th>
                    <th scope="col">{c.colLeft}</th>
                    <th scope="col">{c.colConfirm}</th>
                  </tr>
                </thead>
                <tbody>
                  {upcoming.data.deadlines.map((d) => (
                    <tr key={`${d.contract_id}|${d.kind}`}>
                      <td>{d.date}</td>
                      <th scope="row">
                        <button type="button" onClick={() => setSelected(d.contract_id)}>
                          {d.title || d.contract_id}
                        </button>
                      </th>
                      <td>{kindText(d.kind, d.label)}</td>
                      <td className={d.days_left < 0 ? "contracts-attn" : ""}>{daysText(d.days_left)}</td>
                      <td className={d.verified ? "contracts-ok" : "contracts-attn"}>{d.verified ? c.verified : c.unverified}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
        <div>
          {selected && (
            <DetailPanel
              key={`${tenant}|${selected}`}
              tenant={tenant}
              contractId={selected}
              onNotice={setNotice}
              onChanged={reloadAll}
              onClose={() => setSelected(null)}
            />
          )}
        </div>
      </div>
    </div>
  );
}
