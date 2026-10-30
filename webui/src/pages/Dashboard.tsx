import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, LOGIN_URL } from "../api/client";
import { overviewApi, type MetricsSummary } from "../api/overview";
import { ErrorState, Loading } from "../components/Feedback";
import { jaOverview } from "../i18n/overview";
import { number, percent } from "../lib/format";
import { useLoad } from "../lib/useLoad";
import { Block } from "./overview/Block";

const d = jaOverview.dashboard;

const sum = (values: Record<string, number> | undefined) => Object.values(values ?? {}).reduce((a, b) => a + Number(b || 0), 0);

function Metrics({ metrics }: { metrics: MetricsSummary }) {
  const modes = metrics.counters.chat_answer_mode_total ?? {};
  const total = sum(modes);
  const served = (modes.grounded ?? 0) + (modes.approved_exact_match ?? 0) + (modes.approved_alias_match ?? 0);
  const feedback = metrics.counters.chat_feedback_total ?? {};
  const signals = metrics.alerts?.signals ?? [];
  const overall = metrics.alerts?.overall;
  const rows = Object.entries(modes).sort((a, b) => b[1] - a[1]);
  return (
    <>
      <p className="muted">{metrics.scope === "all_workers" ? d.scopeAll : d.scopeOne}</p>
      <div className="kpis">
        <div className="kpi">
          <b>{number(total)}</b>
          <span>{d.kpiAnswers}</span>
        </div>
        <div className="kpi">
          <b>{percent(served, total)}</b>
          <span>{d.kpiGrounded}</span>
        </div>
        <div className="kpi">
          <b>{percent(modes.fallback ?? 0, total)}</b>
          <span>{d.kpiAbstain}</span>
        </div>
        <div className="kpi">
          <b>
            {feedback.good ?? 0} / {feedback.bad ?? 0}
          </b>
          <span>{d.kpiFeedback}</span>
        </div>
      </div>
      <div className="ov-grid">
        <section className="panel">
          <h2>
            {d.alerts}{" "}
            {overall && (
              <span className={`ov-status ${overall}`} aria-label={`${d.overall}: ${jaOverview.alertState[overall] ?? overall}`}>
                {jaOverview.alertState[overall] ?? overall}
              </span>
            )}
          </h2>
          {signals.length === 0 ? (
            <p className="empty">{d.noSignals}</p>
          ) : (
            <div className="ov-scroll">
              <table>
                <thead>
                  <tr>
                    <th scope="col">{d.colMetric}</th>
                    <th scope="col">{d.colState}</th>
                    <th scope="col">{d.colValue}</th>
                  </tr>
                </thead>
                <tbody>
                  {signals.map((s) => (
                    <tr key={s.name}>
                      <th scope="row">{jaOverview.signals[s.name] ?? s.name}</th>
                      <td>
                        <span className={`ov-status ${s.status}`}>{jaOverview.alertState[s.status] ?? s.status}</span>
                      </td>
                      <td>{String(s.detail || s.value || "")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
        <section className="panel">
          <h2>{d.modes}</h2>
          {rows.length === 0 ? (
            <p className="empty">{d.noData}</p>
          ) : (
            <table>
              <tbody>
                {rows.map(([key, count]) => (
                  <tr key={key}>
                    <th scope="row">{jaOverview.modeLabels[key] ?? key}</th>
                    <td className="ov-bartrack" aria-hidden="true">
                      <span className="ov-bar" style={{ width: `${total ? (count / total) * 100 : 0}%` }} />
                    </td>
                    <td>
                      {number(count)} ({percent(count, total)})
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>
    </>
  );
}

function Banner() {
  const status = useLoad(overviewApi.systemStatus, [], d.systemFailed);
  if (status.error) return <ErrorState message={status.error} onRetry={status.reload} />;
  if (!status.data) return null;
  const llmFailing = status.data.integrations.some((i) => i.name === "llm" && i.state === "failing");
  const warnings = status.data.warnings;
  if (!llmFailing && warnings.length === 0) return null;
  return (
    <div role="note" className="warn">
      <strong>{d.bannerTitle}</strong>
      <ul>
        {llmFailing && <li>{d.bannerLlm}</li>}
        {warnings.length > 0 && <li>{d.bannerOff}</li>}
      </ul>
      <Link to="/system">{d.bannerLink}</Link>
    </div>
  );
}

function Session() {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  async function end() {
    setBusy(true);
    setNotice("");
    try {
      await overviewApi.logoutAll();
      window.location.assign(LOGIN_URL);
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) setNotice(e instanceof ApiError && e.status === 403 ? jaOverview.common.forbidden : d.logoutFailed);
      setAsking(false);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel">
      <h2>{d.session}</h2>
      <p className="muted">{d.sessionHelp}</p>
      {notice && (
        <p role="alert" className="error">
          {notice}
        </p>
      )}
      <button type="button" disabled={busy} onClick={() => setAsking(true)}>
        {d.logoutAll}
      </button>
      {asking && (
        <div role="dialog" aria-modal="true" aria-labelledby="logout-title" className="dialog">
          <h2 id="logout-title">{d.logoutAll}</h2>
          <p>{d.logoutConfirm}</p>
          <button type="button" disabled={busy} onClick={end}>
            {d.logoutDo}
          </button>
          <button type="button" disabled={busy} onClick={() => setAsking(false)}>
            {jaOverview.common.cancel}
          </button>
        </div>
      )}
    </section>
  );
}

export function DashboardPage() {
  const metrics = useLoad(overviewApi.metrics, [], d.metricsFailed);
  const health = useLoad(overviewApi.health, [], d.depsFailed);
  const jobs = useLoad(overviewApi.jobs, [], d.jobsFailed);
  const stats = useLoad(() => overviewApi.recordStats(7), [], d.recordsFailed);
  const daily = useLoad(() => overviewApi.usageDaily(7), [], d.llmFailed);

  return (
    <section>
      <h1>{d.title}</h1>
      <Banner />
      {metrics.error ? (
        <ErrorState message={d.metricsFailed} onRetry={metrics.reload} />
      ) : metrics.data ? (
        <Metrics metrics={metrics.data} />
      ) : (
        metrics.loading && <Loading />
      )}

      <div className="ov-grid">
        <Block title={d.deps} state={health} failureText={d.depsFailed}>
          {(h) => {
            const deps = Object.entries(h.dependencies ?? {});
            const model = [h.embed_provider, h.embed_model].filter(Boolean).join(" / ");
            return (
              <>
                {deps.length === 0 ? (
                  <p className="empty">{d.noDeps}</p>
                ) : (
                  <table>
                    <thead>
                      <tr>
                        <th scope="col">{d.colService}</th>
                        <th scope="col">{d.colState}</th>
                        <th scope="col">{d.colResponse}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {deps.map(([name, dep]) => (
                        <tr key={name}>
                          <th scope="row">{name}</th>
                          <td>
                            <span className={`ov-status ${dep.ok ? "OK" : "CRITICAL"}`}>{dep.ok ? d.depOk : d.depDown}</span>
                          </td>
                          <td>{dep.ok ? `${dep.ms ?? 0} ms` : (dep.error ?? "")}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                {model && (
                  <p className="muted">
                    {d.embedding}: {model}
                  </p>
                )}
              </>
            );
          }}
        </Block>

        <Block
          title={
            <>
              {d.jobs} <Link to="/ingestion" className="muted">{d.jobsAll}</Link>
            </>
          }
          state={jobs}
          failureText={d.jobsFailed}
        >
          {(res) =>
            res.jobs.length === 0 ? (
              <p className="empty">{d.noJobs}</p>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th scope="col">{d.colJob}</th>
                    <th scope="col">{d.colState}</th>
                    <th scope="col">{d.colCollection}</th>
                  </tr>
                </thead>
                <tbody>
                  {res.jobs.slice(0, 8).map((job, i) => (
                    <tr key={job.job_id ?? i}>
                      <th scope="row">{String(job.job_id ?? "").slice(0, 8)}</th>
                      <td>{job.status}</td>
                      <td>{job.collection ?? ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )
          }
        </Block>

        <Block
          title={
            <>
              {d.records} <Link to="/records" className="muted">{d.recordsLink}</Link>
            </>
          }
          state={stats}
          failureText={d.recordsFailed}
        >
          {(s) => (
            <div className="kpis">
              {(
                [
                  [d.recTotal, s.total],
                  [d.recAbstained, s.abstained],
                  [d.recDegraded, s.degraded],
                  [d.recFeedback, sum(s.feedback)],
                  [d.recVerdicts, sum(s.verdicts)],
                  [d.recUnreviewed, s.unreviewed_with_negative_feedback],
                ] as const
              ).map(([label, value]) => (
                <div className="kpi" key={label}>
                  <b>{number(value)}</b>
                  <span>{label}</span>
                </div>
              ))}
            </div>
          )}
        </Block>

        <Block
          title={
            <>
              {d.llm7} <Link to="/usage" className="muted">{d.llmLink}</Link>
            </>
          }
          state={daily}
          failureText={d.llmFailed}
        >
          {(res) => {
            const calls = res.rows.reduce((a, r) => a + r.calls, 0);
            const errors = res.rows.reduce((a, r) => a + r.errors, 0);
            const cost = res.rows.reduce((a, r) => a + r.cost, 0);
            return calls === 0 ? (
              <p className="empty">{d.llmNone}</p>
            ) : (
              <div className="kpis">
                <div className="kpi">
                  <b>{number(calls)}</b>
                  <span>{d.llmCalls}</span>
                </div>
                <div className="kpi">
                  <b>{number(errors)}</b>
                  <span>{d.llmErrors}</span>
                </div>
                <div className="kpi">
                  <b>{cost.toFixed(4)}</b>
                  <span>{d.llmCost}</span>
                </div>
              </div>
            );
          }}
        </Block>

        <Session />
      </div>
    </section>
  );
}
