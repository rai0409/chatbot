import { changesApi } from "../../api/changes";
import { ErrorState, Loading } from "../../components/Feedback";
import { jaChanges as c } from "../../i18n/changes";
import { dateTime } from "../../lib/format";
import { useLoad } from "../../lib/useLoad";
import { DecisionBadge, shortDigest } from "./common";

export function LedgerTab({ onOpenProposal }: { onOpenProposal: (id: string) => void }) {
  const { data, error, loading, reload } = useLoad(() => changesApi.evaluations(), [], c.loadFailed);
  if (error) return <ErrorState message={error} onRetry={reload} />;
  if (!data || loading) return <Loading />;
  return (
    <section>
      <p className="muted">{c.ledger.intro}</p>
      {data.chain_intact ? (
        <p role="status" className="notice">
          {c.ledger.intact}
        </p>
      ) : (
        <p role="alert" className="error">
          {c.ledger.broken}
        </p>
      )}
      <p className="muted">{c.ledger.total(data.total)}</p>
      {data.entries.length === 0 ? (
        <p className="empty">{c.ledger.empty}</p>
      ) : (
        <div className="ch-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">{c.ledger.colSeq}</th>
                <th scope="col">{c.ledger.colAt}</th>
                <th scope="col">{c.ledger.colRun}</th>
                <th scope="col">{c.ledger.colDecision}</th>
                <th scope="col">{c.ledger.colProposal}</th>
                <th scope="col">{c.ledger.colSet}</th>
              </tr>
            </thead>
            <tbody>
              {data.entries.map((entry) => (
                <tr key={entry.seq}>
                  <th scope="row">{entry.seq}</th>
                  <td>{dateTime(entry.at)}</td>
                  <td>{entry.run_id ?? "—"}</td>
                  <td>
                    <DecisionBadge decision={entry.decision} />
                  </td>
                  <td>
                    {entry.proposal_id ? (
                      <button type="button" className="ch-link" onClick={() => onOpenProposal(entry.proposal_id as string)}>
                        {entry.proposal_id}
                      </button>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td>
                    <code title={entry.eval_set_sha256}>{shortDigest(entry.eval_set_sha256)}</code>
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
