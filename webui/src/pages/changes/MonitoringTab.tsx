import { useState } from "react";
import { changesApi } from "../../api/changes";
import { ErrorState, Loading } from "../../components/Feedback";
import { jaChanges as c } from "../../i18n/changes";
import { number } from "../../lib/format";
import { useLoad } from "../../lib/useLoad";
import { StateBadge } from "./common";

const KINDS = ["good", "bad", "neutral", "human_review_requested"] as const;
const DAY_CHOICES = [7, 14, 30, 90];

function total(counts: Record<string, number> | undefined): number {
  return Object.values(counts ?? {}).reduce((sum, n) => sum + n, 0);
}

export function MonitoringTab({ onOpenProposal }: { onOpenProposal: (id: string) => void }) {
  const [days, setDays] = useState(14);
  const { data, error, loading, reload } = useLoad(() => changesApi.monitoring(days), [days], c.loadFailed);
  if (error) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return <Loading />;

  const dayRows = Object.entries(data.feedback_by_day).sort(([a], [b]) => (a < b ? 1 : -1));
  const max = Math.max(1, ...dayRows.flatMap(([, counts]) => KINDS.map((k) => counts[k] ?? 0)));
  const rec = data.records;
  return (
    <section aria-busy={loading}>
      <p className="muted">{c.monitoring.intro}</p>
      <div className="toolbar">
        <label>
          {c.monitoring.days}
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {DAY_CHOICES.map((n) => (
              <option key={n} value={n}>
                {c.monitoring.daysUnit(n)}
              </option>
            ))}
          </select>
        </label>
      </div>

      <h2>{c.monitoring.recordsTitle}</h2>
      <div className="kpis">
        <div className="kpi">
          <b>{number(rec.total)}</b>
          <span>{c.monitoring.total}</span>
        </div>
        <div className="kpi">
          <b>{number(rec.abstained)}</b>
          <span>{c.monitoring.abstained}</span>
        </div>
        <div className="kpi">
          <b>{number(rec.degraded)}</b>
          <span>{c.monitoring.degraded}</span>
        </div>
        <div className="kpi">
          <b>{number(total(rec.feedback))}</b>
          <span>{c.monitoring.feedback}</span>
        </div>
        <div className="kpi">
          <b>{number(total(rec.verdicts))}</b>
          <span>{c.monitoring.verdicts}</span>
        </div>
      </div>

      <h2>{c.monitoring.feedbackTitle}</h2>
      {dayRows.length === 0 ? (
        <p className="empty">{c.monitoring.feedbackEmpty}</p>
      ) : (
        <div className="ch-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">{c.monitoring.colDay}</th>
                {KINDS.map((k) => (
                  <th scope="col" key={k}>
                    {c.monitoring[k]}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {dayRows.map(([day, counts]) => (
                <tr key={day}>
                  <th scope="row">{day}</th>
                  {KINDS.map((k) => (
                    <td key={k}>
                      <span className="ch-bar-row">
                        <span className={`ch-bar ch-bar-${k}`} style={{ width: `${((counts[k] ?? 0) / max) * 100}%` }} aria-hidden="true" />
                        <span>{counts[k] ?? 0}</span>
                      </span>
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h2>{c.monitoring.rolloutTitle}</h2>
      {data.changes_in_rollout.length === 0 ? (
        <p className="empty">{c.monitoring.rolloutEmpty}</p>
      ) : (
        <div className="ch-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">{c.monitoring.colTitle}</th>
                <th scope="col">{c.monitoring.colState}</th>
                <th scope="col">{c.monitoring.colProfile}</th>
              </tr>
            </thead>
            <tbody>
              {data.changes_in_rollout.map((p) => (
                <tr key={p.id}>
                  <th scope="row">
                    <button type="button" className="ch-link" onClick={() => onOpenProposal(p.id)} aria-label={`${c.monitoring.openProposal}: ${p.title}`}>
                      {p.title}
                    </button>
                  </th>
                  <td>
                    <StateBadge state={p.state} />
                  </td>
                  <td>{p.profile_id}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
