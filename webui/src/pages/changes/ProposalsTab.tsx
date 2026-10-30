import { useState } from "react";
import { changesApi } from "../../api/changes";
import { ErrorState, Loading } from "../../components/Feedback";
import { jaChanges as c } from "../../i18n/changes";
import { dateTime } from "../../lib/format";
import { useLoad } from "../../lib/useLoad";
import { ActorField, DecisionBadge, ReadOnlyNote, StateBadge, failureText, isForbidden, useActor, useReadOnly } from "./common";
import { ProposalDetail } from "./ProposalDetail";

const ID_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;

export function ProposalsTab({ initialId }: { initialId?: string }) {
  const [selected, setSelected] = useState(initialId ?? "");
  const [filter, setFilter] = useState("");
  const [states, setStates] = useState<string[]>([]);
  const [creating, setCreating] = useState(false);
  const { readOnly } = useReadOnly();
  const { data, error, loading, reload } = useLoad(
    async () => {
      const list = await changesApi.proposals(filter);
      setStates(list.states);
      return list;
    },
    [filter],
    c.loadFailed,
  );

  if (selected)
    return (
      <ProposalDetail
        id={selected}
        onBack={() => {
          setSelected("");
          reload();
        }}
      />
    );
  if (creating) return <NewProposal onCancel={() => setCreating(false)} onCreated={(id) => { setCreating(false); setSelected(id); }} />;
  if (error) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return <Loading />;

  return (
    <section aria-busy={loading}>
      <p className="muted">{c.intro}</p>
      <ReadOnlyNote />
      <div className="toolbar">
        <label>
          {c.proposals.filter}
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="">{c.proposals.all}</option>
            {states.map((s) => (
              <option key={s} value={s}>
                {c.state[s] ?? s}
              </option>
            ))}
          </select>
        </label>
        <button type="button" disabled={readOnly} onClick={() => setCreating(true)}>
          {c.proposals.create}
        </button>
        <button type="button" onClick={reload}>
          {c.reload}
        </button>
      </div>
      {data.proposals.length === 0 ? (
        <p className="empty">{filter ? c.proposals.emptyFiltered : c.proposals.empty}</p>
      ) : (
        <div className="ch-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">{c.proposals.colTitle}</th>
                <th scope="col">{c.proposals.colState}</th>
                <th scope="col">{c.proposals.colCategory}</th>
                <th scope="col">{c.proposals.colProfile}</th>
                <th scope="col">{c.proposals.colProposer}</th>
                <th scope="col">{c.proposals.colApprovals}</th>
                <th scope="col">{c.proposals.colDecision}</th>
                <th scope="col">{c.proposals.colUpdated}</th>
              </tr>
            </thead>
            <tbody>
              {data.proposals.map((p) => (
                <tr key={p.id}>
                  <th scope="row">
                    <button type="button" className="ch-link" onClick={() => setSelected(p.id)} aria-label={`${c.proposals.open}: ${p.title}`}>
                      {p.title}
                    </button>
                  </th>
                  <td>
                    <StateBadge state={p.state} />
                  </td>
                  <td>{p.category ? (c.category[p.category] ?? p.category) : c.proposals.unclassified}</td>
                  <td>{p.profile_id}</td>
                  <td>{p.proposed_by ?? "—"}</td>
                  <td>{p.approvals_required === null ? "—" : c.detail.approvalsCount(p.approvals.length, p.approvals_required)}</td>
                  <td>
                    <DecisionBadge decision={p.decision} />
                  </td>
                  <td>{dateTime(p.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function NewProposal({ onCancel, onCreated }: { onCancel: () => void; onCreated: (id: string) => void }) {
  const { readOnly, markReadOnly } = useReadOnly();
  const [actor, setActor] = useActor();
  const [id, setId] = useState("");
  const [profile, setProfile] = useState("");
  const [title, setTitle] = useState("");
  const [source, setSource] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState("");
  const idBad = id !== "" && !ID_PATTERN.test(id);
  const ready = ID_PATTERN.test(id) && profile.trim() !== "" && title.trim() !== "" && actor.trim() !== "";

  async function submit() {
    if (!ready) return;
    setBusy(true);
    setFailed("");
    try {
      const created = await changesApi.createProposal({
        id,
        profile_id: profile.trim(),
        title: title.trim(),
        actor: actor.trim(),
        ...(source.trim() ? { source: { note: source.trim() } } : {}),
      });
      onCreated(created.id);
    } catch (e) {
      if (isForbidden(e)) markReadOnly();
      setFailed(failureText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <h2>{c.proposals.newTitle}</h2>
      <ReadOnlyNote />
      <form
        className="ch-form"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <label>
          {c.proposals.newId}
          <input type="text" value={id} maxLength={64} onChange={(e) => setId(e.target.value)} aria-invalid={idBad} />
        </label>
        {idBad && (
          <p role="alert" className="error">
            {c.proposals.idInvalid}
          </p>
        )}
        <label>
          {c.proposals.newProfile}
          <input type="text" value={profile} maxLength={64} onChange={(e) => setProfile(e.target.value)} />
        </label>
        <label>
          {c.proposals.newName}
          <input type="text" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label>
          {c.proposals.newSource}
          <textarea value={source} rows={3} maxLength={1000} onChange={(e) => setSource(e.target.value)} />
        </label>
        <ActorField actor={actor} onChange={setActor} />
        {!ready && <p className="muted">{c.proposals.required}</p>}
        {failed && (
          <p role="alert" className="error">
            {failed}
          </p>
        )}
        <button type="submit" disabled={busy || readOnly || !ready}>
          {c.proposals.newSubmit}
        </button>{" "}
        <button type="button" onClick={onCancel}>
          {c.cancel}
        </button>
      </form>
    </section>
  );
}
