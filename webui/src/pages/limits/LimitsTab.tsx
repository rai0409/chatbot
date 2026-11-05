import { type FormEvent, useState } from "react";
import { limitsApi, type LimitRow, type LimitsSnapshot, type LimitWarning, type LimitName, type UseTotals } from "../../api/limits";
import { jaLimits as j } from "../../i18n/limits";
import { dateTime } from "../../lib/format";
import { useLoad } from "../../lib/useLoad";
import { ErrorState, Loading } from "../../components/Feedback";
import { amount, Notice } from "./common";
import { LimitForm } from "./LimitForm";

const l = j.limits;

interface Use {
  used: number;
  who: string;
}

/** The biggest use today that the limit applies to (one caller, one tenant, or everyone). */
function biggestUse(row: LimitRow, data: LimitsSnapshot, tenant: string): Use | null {
  const unit = row.unit;
  if (row.scope === "global") {
    const used = data.global[unit];
    return used === undefined ? null : { used, who: l.useGlobal };
  }
  const candidates: Use[] = [];
  if (row.scope === "tenant") {
    for (const [id, totals] of Object.entries(data.tenants as Record<string, UseTotals>)) if (!tenant || id === tenant) candidates.push({ used: totals[unit], who: id });
  } else {
    for (const u of data.users) if (!tenant || u.tenant_id === tenant) candidates.push({ used: u[unit], who: u.user });
  }
  return candidates.reduce<Use | null>((best, c) => (best === null || c.used > best.used ? c : best), null);
}

function whoText(w: LimitWarning): string {
  if (w.scope === "global") return l.whoGlobal;
  return w.scope === "tenant" ? l.whoTenant(w.id ?? "") : l.whoUser(w.id ?? "");
}

function UseCell({ row, data, tenant }: { row: LimitRow; data: LimitsSnapshot; tenant: string }) {
  if (row.value <= 0) return <span className="muted">{l.useNone}</span>;
  const use = biggestUse(row, data, tenant);
  if (use === null) return <span className="muted">{l.useNoData}</span>;
  const ratio = use.used / row.value;
  const pct = Math.round(ratio * 100);
  return (
    <div className="lm-use">
      <meter className="lm-meter" min={0} max={1} value={Math.min(ratio, 1)} aria-label={l.meterLabel(l.names[row.name] ?? row.name)}>
        {pct}%
      </meter>
      <span>{l.usePercent(amount(use.used), amount(row.value), pct)}</span>
      <small>{row.scope === "global" ? use.who : l.useTop(use.who)}</small>
    </div>
  );
}

function Warnings({ data }: { data: LimitsSnapshot }) {
  return (
    <section className="panel">
      <h2>{l.warningsTitle}</h2>
      {data.warnings.length === 0 ? (
        <p className="empty">{l.warningsEmpty}</p>
      ) : (
        <div className="lm-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">{l.colWarnLimit}</th>
                <th scope="col">{l.colWarnWho}</th>
                <th scope="col">{l.colWarnUse}</th>
                <th scope="col">{l.colWarnRatio}</th>
                <th scope="col">{l.colWarnState}</th>
              </tr>
            </thead>
            <tbody>
              {data.warnings.map((w) => (
                <tr key={`${w.limit}/${w.scope}/${w.id}`}>
                  <th scope="row">{l.names[w.limit] ?? w.limit}</th>
                  <td className={w.scope === "user" ? "lm-fp" : undefined}>{whoText(w)}</td>
                  <td>
                    {amount(w.used)} / {amount(w.allowed)}
                  </td>
                  <td>{Math.round(w.ratio * 100)}%</td>
                  <td>
                    <span className={`lm-state ${w.ratio >= 1 ? "reached" : "near"}`}>{w.ratio >= 1 ? l.reached : l.approaching}</span>
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

function Usage({ data }: { data: LimitsSnapshot }) {
  const tenants = Object.entries(data.tenants);
  return (
    <>
      <section className="panel">
        <h2>{l.tenantsTitle}</h2>
        {tenants.length === 0 ? (
          <p className="empty">{l.tenantsEmpty}</p>
        ) : (
          <div className="lm-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">{l.colTenant}</th>
                  <th scope="col">{l.colCalls}</th>
                  <th scope="col">{l.colTokens}</th>
                  <th scope="col">{l.colCost}</th>
                </tr>
              </thead>
              <tbody>
                {tenants.map(([id, u]) => (
                  <tr key={id}>
                    <th scope="row">{id}</th>
                    <td>{amount(u.calls)}</td>
                    <td>{amount(u.tokens)}</td>
                    <td>{amount(u.cost)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <section className="panel">
        <h2>{l.usersTitle}</h2>
        <p className="muted">{l.usersNote}</p>
        {data.users.length === 0 ? (
          <p className="empty">{l.usersEmpty}</p>
        ) : (
          <div className="lm-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">{l.colCaller}</th>
                  <th scope="col">{l.colTenant}</th>
                  <th scope="col">{l.colCalls}</th>
                  <th scope="col">{l.colTokens}</th>
                  <th scope="col">{l.colCost}</th>
                </tr>
              </thead>
              <tbody>
                {data.users.map((u) => (
                  <tr key={`${u.tenant_id}/${u.user}`}>
                    <th scope="row" className="lm-fp">
                      {u.user}
                    </th>
                    <td>{u.tenant_id}</td>
                    <td>{amount(u.calls)}</td>
                    <td>{amount(u.tokens)}</td>
                    <td>{amount(u.cost)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}

export function LimitsTab() {
  const [tenantInput, setTenantInput] = useState("");
  const [tenant, setTenant] = useState("");
  const state = useLoad(() => limitsApi.limits(tenant || undefined), [tenant], l.failed);
  const [editing, setEditing] = useState<LimitName | null>(null);
  const [notice, setNotice] = useState("");
  const [forbidden, setForbidden] = useState(false);
  const { data } = state;

  function apply(event: FormEvent) {
    event.preventDefault();
    setTenant(tenantInput.trim());
  }

  return (
    <div>
      <p className="muted">{l.intro}</p>
      <form className="toolbar" onSubmit={apply}>
        <label>
          {l.tenantFilter}
          <input type="text" value={tenantInput} maxLength={64} autoComplete="off" aria-describedby="lm-tenant-help" onChange={(e) => setTenantInput(e.target.value)} />
        </label>
        <button type="submit">{l.tenantApply}</button>
      </form>
      <p id="lm-tenant-help" className="muted">
        {l.tenantFilterHelp}
      </p>
      <Notice text={notice} />
      {forbidden && (
        <p role="note" className="warn">
          {l.form.forbiddenNote}
        </p>
      )}

      {state.error ? (
        <ErrorState message={l.failed} onRetry={state.reload} />
      ) : data === null ? (
        state.loading && <Loading />
      ) : (
        <>
          <div className="kpis">
            <div className="kpi">
              <b>{dateTime(data.day_starts_at)}</b>
              <span>{l.dayStarts}</span>
            </div>
            <div className="kpi">
              <b>{l.warnRatioValue(data.warn_ratio)}</b>
              <span>{l.warnRatio}</span>
            </div>
          </div>

          <section className="panel">
            <h2>{l.tableTitle}</h2>
            <div className="lm-scroll">
              <table>
                <thead>
                  <tr>
                    <th scope="col">{l.colName}</th>
                    <th scope="col">{l.colValue}</th>
                    <th scope="col">{l.colSource}</th>
                    <th scope="col">{l.colUse}</th>
                    <th scope="col">{l.colAction}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.limits.map((row) => (
                    <tr key={row.name}>
                      <th scope="row">{l.names[row.name] ?? row.name}</th>
                      <td>{row.value > 0 ? `${amount(row.value)} ${l.units[row.unit] ?? ""}` : l.noLimit}</td>
                      <td>{l.sources[row.source] ?? row.source}</td>
                      <td>
                        <UseCell row={row} data={data} tenant={tenant} />
                      </td>
                      <td>
                        <button type="button" aria-label={l.changeFor(l.names[row.name] ?? row.name)} onClick={() => setEditing(row.name as LimitName)}>
                          {l.change}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <Warnings data={data} />
          <Usage data={data} />
        </>
      )}

      {editing && (
        <LimitForm
          initialName={editing}
          initialTenant={tenant}
          onCancel={() => setEditing(null)}
          onForbidden={() => {
            setEditing(null);
            setNotice("");
            setForbidden(true);
          }}
          onDone={() => {
            setEditing(null);
            setForbidden(false);
            setNotice(l.form.saved);
            state.reload();
          }}
        />
      )}
    </div>
  );
}
