import { useState } from "react";
import { type MoveName, changesApi } from "../../api/changes";
import { ErrorState, Loading } from "../../components/Feedback";
import { jaChanges as c } from "../../i18n/changes";
import { dateTime } from "../../lib/format";
import { useLoad } from "../../lib/useLoad";
import { ActorField, DecisionBadge, ReadOnlyNote, StateBadge, knobText, shortDigest, useActor, useReadOnly } from "./common";
import { MoveForm, allowedMoves } from "./MoveForm";

function evidenceText(evidence: Record<string, unknown>): string {
  const parts = Object.entries(evidence).map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`);
  return parts.length ? parts.join(" / ") : "—";
}

export function ProposalDetail({ id, onBack }: { id: string; onBack: () => void }) {
  const { data, error, loading, reload } = useLoad(() => changesApi.proposal(id), [id], c.detail.loadFailed);
  const { readOnly } = useReadOnly();
  const [actor, setActor] = useActor();
  const [move, setMove] = useState<MoveName | null>(null);
  const [notice, setNotice] = useState("");

  if (error) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return <Loading />;

  const p = data;
  const moves = allowedMoves(p);
  const required = p.summary.approvals_required;
  return (
    <section aria-busy={loading}>
      <p>
        <button type="button" onClick={onBack}>
          {c.back}
        </button>
      </p>
      <h2>{p.title}</h2>
      <ReadOnlyNote />
      {notice && (
        <p role="status" className="notice">
          {notice}
        </p>
      )}
      <div className="panel">
        <h3>{c.detail.summary}</h3>
        <dl className="ch-dl">
          <dt>{c.detail.id}</dt>
          <dd>
            <code>{p.id}</code>
          </dd>
          <dt>{c.proposals.colState}</dt>
          <dd>
            <StateBadge state={p.state} />
          </dd>
          <dt>{c.proposals.colCategory}</dt>
          <dd>{p.category ? (c.category[p.category] ?? p.category) : c.proposals.unclassified}</dd>
          <dt>{c.proposals.colProfile}</dt>
          <dd>{p.profile_id}</dd>
          <dt>{c.proposals.colProposer}</dt>
          <dd>{p.proposed_by ?? "—"}</dd>
          {Object.keys(p.source ?? {}).length > 0 && (
            <>
              <dt>{c.detail.source}</dt>
              <dd>{evidenceText(p.source)}</dd>
            </>
          )}
        </dl>
      </div>

      <div className="panel">
        <h3>{c.detail.change}</h3>
        {!p.change ? (
          <p className="empty">{c.detail.noChange}</p>
        ) : (
          <dl className="ch-dl">
            {p.change.profile_version && (
              <>
                <dt>{c.detail.profileVersion}</dt>
                <dd>
                  <code>{p.change.profile_version}</code>
                </dd>
              </>
            )}
            {p.change.knobs && (
              <>
                <dt>{c.detail.knobs}</dt>
                <dd>
                  <ul>
                    {Object.entries(p.change.knobs).map(([name, value]) => (
                      <li key={name}>
                        <code>{name}</code> = {knobText(value)}
                      </li>
                    ))}
                  </ul>
                </dd>
              </>
            )}
            {p.change.manual && (
              <>
                <dt>{c.detail.manual}</dt>
                <dd>{p.change.manual}</dd>
              </>
            )}
          </dl>
        )}
      </div>

      <div className="panel">
        <h3>{c.detail.evaluation}</h3>
        {!p.evaluation ? (
          <p className="empty">{c.detail.noEvaluation}</p>
        ) : (
          <dl className="ch-dl">
            <dt>{c.detail.ledgerSeq}</dt>
            <dd>#{p.evaluation.ledger_seq}</dd>
            <dt>{c.detail.run}</dt>
            <dd>{p.evaluation.run_id ?? "—"}</dd>
            <dt>{c.detail.decision}</dt>
            <dd>
              <DecisionBadge decision={p.evaluation.decision} />
            </dd>
            <dt>{c.ledger.colSet}</dt>
            <dd>
              <code>{shortDigest(p.evaluation.eval_set_sha256)}</code>
            </dd>
          </dl>
        )}
        <h4>{c.detail.approvals}</h4>
        {required !== null && (
          <p>
            {c.detail.approvalsCount(p.approvals.length, required)}
            {required > 1 && <span className="muted"> ・ {c.detail.highRisk}</span>}
          </p>
        )}
        {p.approvals.length === 0 ? (
          <p className="muted">{c.detail.noApprovals}</p>
        ) : (
          <ul>
            {p.approvals.map((a) => (
              <li key={`${a.actor}-${a.at}`}>
                {a.actor} ({dateTime(a.at)}){a.note ? ` ${a.note}` : ""}
                {a.accepted_insufficient ? ` ・ ${c.detail.acceptedInsufficient}: ${a.accepted_insufficient}` : ""}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="panel">
        <h3>{c.moves.title}</h3>
        <ActorField actor={actor} onChange={setActor} />
        {moves.length === 0 ? (
          <p className="muted">{c.moves.none}</p>
        ) : (
          <div className="toolbar" role="group" aria-label={c.moves.title}>
            {moves.map((m) => (
              <button key={m} type="button" disabled={readOnly} aria-pressed={move === m} onClick={() => setMove(m)}>
                {c.moves[m]}
              </button>
            ))}
          </div>
        )}
        {move && moves.includes(move) && (
          <MoveForm
            key={`${move}-${p.rev}`}
            proposal={p}
            move={move}
            actor={actor}
            onCancel={() => setMove(null)}
            onDone={() => {
              setMove(null);
              setNotice(c.moves.done);
              reload();
            }}
          />
        )}
      </div>

      <h3>{c.detail.history}</h3>
      <div className="ch-scroll">
        <table>
          <thead>
            <tr>
              <th scope="col">{c.detail.colAt}</th>
              <th scope="col">{c.detail.colActor}</th>
              <th scope="col">{c.detail.colMove}</th>
              <th scope="col">{c.detail.colNote}</th>
              <th scope="col">{c.detail.colEvidence}</th>
            </tr>
          </thead>
          <tbody>
            {p.history.map((h, i) => (
              <tr key={i}>
                <th scope="row">{dateTime(h.at)}</th>
                <td>{h.actor}</td>
                <td>
                  {h.from ? (c.state[h.from] ?? h.from) : "—"} → {c.state[h.to] ?? h.to}
                </td>
                <td>{h.note || "—"}</td>
                <td>{evidenceText(h.evidence ?? {})}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
