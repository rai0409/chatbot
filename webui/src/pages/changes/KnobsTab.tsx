import { changesApi } from "../../api/changes";
import { ErrorState, Loading } from "../../components/Feedback";
import { jaChanges as c } from "../../i18n/changes";
import { useLoad } from "../../lib/useLoad";
import { knobText } from "./common";

export function KnobsTab() {
  const { data, error, loading, reload } = useLoad(() => changesApi.knobs(), [], c.loadFailed);
  if (error) return <ErrorState message={error} onRetry={reload} />;
  if (!data || loading) return <Loading />;
  return (
    <section>
      <p className="muted">{c.knobs.intro}</p>
      {data.knobs.length === 0 ? (
        <p className="empty">{c.knobs.empty}</p>
      ) : (
        <div className="ch-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">{c.knobs.colName}</th>
                <th scope="col">{c.knobs.colStage}</th>
                <th scope="col">{c.knobs.colKind}</th>
                <th scope="col">{c.knobs.colLimits}</th>
                <th scope="col">{c.knobs.colRisk}</th>
                <th scope="col">{c.knobs.colValue}</th>
                <th scope="col">{c.knobs.colDescription}</th>
              </tr>
            </thead>
            <tbody>
              {data.knobs.map((k) => (
                <tr key={k.name}>
                  <th scope="row">
                    <code>{k.name}</code>
                  </th>
                  <td>{k.stage}</td>
                  <td>{c.knobs.kind[k.kind] ?? k.kind}</td>
                  <td>{k.lo === null && k.hi === null ? c.knobs.noLimit : `${k.lo ?? "—"} 〜 ${k.hi ?? "—"}`}</td>
                  <td>
                    <span className={`badge ${k.risk === "high" ? "failing" : "ok"}`}>{k.risk === "high" ? c.knobs.high : c.knobs.low}</span>
                  </td>
                  <td>{knobText(k.value)}</td>
                  <td>{k.description}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
