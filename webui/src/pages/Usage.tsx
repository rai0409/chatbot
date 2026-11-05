import { useState } from "react";
import { ApiError } from "../api/client";
import { USAGE_GROUPS, overviewApi, type Availability, type Downtime, type UsageGroup } from "../api/overview";
import { jaOverview } from "../i18n/overview";
import { number } from "../lib/format";
import { useLoad } from "../lib/useLoad";
import { Block } from "./overview/Block";

const u = jaOverview.usage;
const PERIODS = [1, 7, 30, 90];

/** Seconds as hours and minutes (under a minute: seconds). */
export function duration(seconds: number): string {
  const total = Math.round(seconds);
  if (total < 60) return `${total}${u.seconds}`;
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  return hours > 0 ? `${hours}${u.hours}${minutes}${u.minutes}` : `${minutes}${u.minutes}`;
}

type DowntimeResult = { forbidden: true } | { forbidden: false; report: Downtime };

async function loadDowntime(days: number): Promise<DowntimeResult> {
  try {
    return { forbidden: false, report: await overviewApi.downtime(days) };
  } catch (e) {
    if (e instanceof ApiError && e.status === 403) return { forbidden: true };
    throw e;
  }
}

type AvailabilityResult = { forbidden: true } | { forbidden: false; report: Availability };

async function loadAvailability(days: number): Promise<AvailabilityResult> {
  try {
    return { forbidden: false, report: await overviewApi.availability(days) };
  } catch (e) {
    if (e instanceof ApiError && e.status === 403) return { forbidden: true };
    throw e;
  }
}

function AvailabilityBody({ report }: { report: Availability }) {
  if (!report.configured) return <p role="note" className="warn">{u.availabilityNotConfigured}</p>;
  if (report.log_file_found === false) return <p role="note" className="warn">{u.availabilityNoFile}</p>;
  const minutes = report.business_minutes;
  if (report.availability_percent == null || !minutes) return <p className="empty">{u.availabilityNoData}</p>;
  const outages = report.outages ?? [];
  return (
    <>
      <p>{u.availabilityHelp}</p>
      <p>
        <strong>{report.availability_percent}%</strong>
        {report.meets_target != null && (
          <span className={`badge ${report.meets_target ? "ok" : "failing"}`}>
            {u.availabilityTarget} {report.target_percent}%: {report.meets_target ? u.availabilityMet : u.availabilityMissed}
          </span>
        )}
      </p>
      <p className="muted">
        {u.availabilityUp} {u.availabilityMinutes(minutes.up)} ・ {u.availabilityDown} {u.availabilityMinutes(minutes.down)} ・{" "}
        {u.availabilityUnmonitored} {u.availabilityMinutes(minutes.unmonitored)} ・ {u.availabilityCoverage} {report.coverage_percent ?? "—"}% ・{" "}
        {u.availabilityDegraded} {u.availabilityMinutes(minutes.degraded_but_up)}
      </p>
      {outages.length > 0 && (
        <>
          <h3>{u.availabilityOutages}</h3>
          <ul>
            {outages.map((o) => (
              <li key={o.start}>{u.availabilityOutageRow(o.start, o.end ?? u.availabilityOngoing, o.business_minutes)}</li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}

export function UsagePage() {
  const [days, setDays] = useState(7);
  const [by, setBy] = useState<UsageGroup>("tenant_id");
  const usage = useLoad(() => overviewApi.usage(days, by), [days, by], u.failed);
  const daily = useLoad(() => overviewApi.usageDaily(days), [days], u.dailyFailed);
  const downtime = useLoad(() => loadDowntime(days), [days], u.downtimeFailed);
  const availability = useLoad(() => loadAvailability(days), [days], u.availabilityFailed);

  return (
    <section>
      <h1>{u.title}</h1>
      <p className="muted">{u.intro}</p>
      <div className="ov-actions">
        <label>
          {u.period}
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {PERIODS.map((n) => (
              <option key={n} value={n}>
                {u.days(n)}
              </option>
            ))}
          </select>
        </label>
        <label>
          {u.groupBy}
          <select value={by} onChange={(e) => setBy(e.target.value as UsageGroup)}>
            {USAGE_GROUPS.map((g) => (
              <option key={g} value={g}>
                {u.groups[g]}
              </option>
            ))}
          </select>
        </label>
      </div>

      <Block title={u.groups[by]} state={usage} failureText={u.failed}>
        {(res) =>
          res.rows.length === 0 ? (
            <p className="empty">{u.empty}</p>
          ) : (
            <div className="ov-scroll">
              <table>
                <thead>
                  <tr>
                    <th scope="col">{u.groups[by]}</th>
                    <th scope="col">{u.colCalls}</th>
                    <th scope="col">{u.colOk}</th>
                    <th scope="col">{u.colError}</th>
                    <th scope="col">{u.colRefused}</th>
                    <th scope="col">{u.colBreaker}</th>
                    <th scope="col">{u.colTokens}</th>
                    <th scope="col">{u.colCost}</th>
                    <th scope="col">{u.colLatency}</th>
                  </tr>
                </thead>
                <tbody>
                  {res.rows.map((row, i) => (
                    <tr key={`${row[by]}-${i}`}>
                      <th scope="row">{String(row[by] || u.blank)}</th>
                      <td>{number(row.calls)}</td>
                      <td>{number(row.ok)}</td>
                      <td>{number(row.error)}</td>
                      <td>{number(row.refused)}</td>
                      <td>{number(row.breaker_open)}</td>
                      <td>{number(row.input_tokens + row.output_tokens)}</td>
                      <td>{row.cost.toFixed(4)}</td>
                      <td>{row.calls ? `${number(Math.round(row.latency_ms / row.calls))} ${u.ms}` : jaOverview.common.none}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </Block>

      <Block title={u.daily} state={daily} failureText={u.dailyFailed}>
        {(res) => {
          const peak = Math.max(1, ...res.rows.map((r) => r.calls));
          return res.rows.length === 0 ? (
            <p className="empty">{u.dailyEmpty}</p>
          ) : (
            <>
              <p className="muted">{u.graphNote}</p>
              <div className="ov-scroll">
                <table>
                  <thead>
                    <tr>
                      <th scope="col">{u.colDay}</th>
                      <th scope="col">{u.colGraph}</th>
                      <th scope="col">{u.colCalls}</th>
                      <th scope="col">{u.colError}</th>
                      <th scope="col">{u.colTokens}</th>
                      <th scope="col">{u.colCost}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {res.rows.map((row) => (
                      <tr key={row.day}>
                        <th scope="row">{row.day}</th>
                        <td className="ov-bartrack" aria-hidden="true">
                          <span className="ov-bar" style={{ width: `${(row.calls / peak) * 100}%` }} />
                        </td>
                        <td>{number(row.calls)}</td>
                        <td>{number(row.errors)}</td>
                        <td>{number(row.input_tokens + row.output_tokens)}</td>
                        <td>{row.cost.toFixed(4)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          );
        }}
      </Block>

      <Block title={u.availability} state={availability} failureText={u.availabilityFailed}>
        {(res) => (res.forbidden ? <p role="note" className="warn">{u.availabilityForbidden}</p> : <AvailabilityBody report={res.report} />)}
      </Block>

      <Block title={u.downtime} state={downtime} failureText={u.downtimeFailed}>
        {(res) => {
          if (res.forbidden) return <p role="note" className="warn">{u.downtimeForbidden}</p>;
          const { report } = res;
          const rows = Object.entries(report.by_breaker);
          return (
            <>
              <p>{u.downtimeHelp}</p>
              <p className="muted">
                {u.businessHours(report.business_hours)} ・ {u.windowBusiness}: {duration(report.window_business_seconds)}
              </p>
              {rows.length === 0 ? (
                <p className="empty">{u.downtimeEmpty}</p>
              ) : (
                <div className="ov-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th scope="col">{u.colBreakerKey}</th>
                        <th scope="col">{u.colOpened}</th>
                        <th scope="col">{u.colOpen}</th>
                        <th scope="col">{u.colOpenBusiness}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map(([key, v]) => (
                        <tr key={key}>
                          <th scope="row">{key}</th>
                          <td>{number(v.opened)}</td>
                          <td>{duration(v.open_seconds)}</td>
                          <td>{duration(v.open_business_seconds)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          );
        }}
      </Block>
    </section>
  );
}
