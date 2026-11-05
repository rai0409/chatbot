import { useState } from "react";
import { type Knob, type KnobValue, type MoveBody, type MoveName, type Proposal, type ProposalChange, changesApi } from "../../api/changes";
import { ErrorState, Loading } from "../../components/Feedback";
import { jaChanges as c } from "../../i18n/changes";
import { useLoad } from "../../lib/useLoad";
import { DecisionBadge, failureText, isForbidden, knobText, useReadOnly } from "./common";

export const NEXT_STATE: Record<string, string> = { approved: "shadow", shadow: "canary", canary: "live", live: "monitoring" };
export const PROFILE_CATEGORIES = ["synonym", "boost_rule", "validation_rule"];
export const KNOB_CATEGORIES = ["guard_threshold", "retrieval_setting"];
export const MANUAL_CATEGORIES = ["ingest_gap", "stale_content", "other"];

/** The moves the current state allows (the state machine of eval/tenant_eval/proposals.py). */
export function allowedMoves(p: Pick<Proposal, "state" | "change">): MoveName[] {
  const manual = !!p.change && "manual" in p.change;
  switch (p.state) {
    case "observed":
      return ["classify", "reject"];
    case "classified":
      return ["propose", "reject"];
    case "proposed":
      return manual ? ["retire", "reject"] : ["attach-evaluation", "reject"];
    case "evaluated":
      return ["approve", "propose", "reject"];
    case "approved":
    case "shadow":
    case "canary":
    case "live":
      return ["advance", "retire"];
    case "monitoring":
      return ["retire"];
    default:
      return [];
  }
}

interface Row {
  name: string;
  value: string;
}

/** Check the knob rows against the kind and limits of each setting. Returns the error of each row, and the change if all is well. */
export function checkKnobRows(rows: Row[], knobs: Knob[]): { errors: string[]; change: Record<string, KnobValue> | null } {
  const seen = new Set<string>();
  const change: Record<string, KnobValue> = {};
  const errors = rows.map((row) => {
    const knob = knobs.find((k) => k.name === row.name);
    if (!knob) return c.moves.knobPick;
    if (seen.has(row.name)) return c.moves.knobDuplicate;
    seen.add(row.name);
    if (row.value === "") return c.moves.knobNeedValue;
    if (knob.kind === "bool") {
      change[row.name] = row.value === "true";
      return "";
    }
    const n = Number(row.value);
    if (!Number.isFinite(n)) return c.moves.knobNotNumber;
    if (knob.kind === "int" && !Number.isInteger(n)) return c.moves.knobNotInt;
    if ((knob.lo !== null && n < knob.lo) || (knob.hi !== null && n > knob.hi)) return c.moves.knobOutOfRange(knob.lo, knob.hi);
    change[row.name] = n;
    return "";
  });
  return { errors, change: errors.every((e) => !e) ? change : null };
}

interface Props {
  proposal: Proposal;
  move: MoveName;
  actor: string;
  onDone: () => void;
  onCancel: () => void;
}

export function MoveForm({ proposal, move, actor, onDone, onCancel }: Props) {
  const { readOnly, markReadOnly } = useReadOnly();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState("");
  const [note, setNote] = useState("");
  const [text, setText] = useState(""); // reason / evidence
  const [category, setCategory] = useState("");
  const [accept, setAccept] = useState("");
  const [seq, setSeq] = useState("");
  const [change, setChange] = useState<{ change: ProposalChange | null; problem: string }>({ change: null, problem: c.moves.needChange });

  const evaluation = proposal.evaluation;
  const decision = evaluation?.decision ?? null;

  let body: MoveBody | null = null;
  const base = { actor: actor.trim() };
  if (actor.trim()) {
    if (move === "classify" && category) body = { ...base, category, note: note.trim() };
    else if (move === "propose" && change.change) body = { ...base, change: change.change, note: note.trim() };
    else if (move === "attach-evaluation" && seq) body = { ...base, ledger_seq: Number(seq) };
    else if (move === "approve" && decision !== "fail" && (decision !== "insufficient" || accept.trim()))
      body = { ...base, note: note.trim(), ...(decision === "insufficient" ? { accept_insufficient_reason: accept.trim() } : {}) };
    else if ((move === "reject" || move === "retire") && text.trim()) body = { ...base, reason: text.trim() };
    else if (move === "advance" && text.trim()) body = { ...base, evidence: text.trim() };
  }

  async function submit() {
    if (!body) return;
    setBusy(true);
    setFailed("");
    try {
      await changesApi.move(proposal.id, move, body);
      onDone();
    } catch (e) {
      if (isForbidden(e)) markReadOnly();
      setFailed(failureText(e));
    } finally {
      setBusy(false);
    }
  }

  const title = c.moves[move];
  return (
    <form
      className="panel ch-form"
      aria-label={title}
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <h3>{title}</h3>

      {move === "classify" && (
        <>
          <label>
            {c.moves.category}
            <select value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="">{c.moves.categoryPick}</option>
              {[
                [c.categoryGroup.profile, PROFILE_CATEGORIES],
                [c.categoryGroup.knob, KNOB_CATEGORIES],
                [c.categoryGroup.manual, MANUAL_CATEGORIES],
              ].map(([label, items]) => (
                <optgroup key={label as string} label={label as string}>
                  {(items as string[]).map((item) => (
                    <option key={item} value={item}>
                      {c.category[item]}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </label>
          <NoteField value={note} onChange={setNote} />
        </>
      )}

      {move === "propose" && (
        <>
          {proposal.state === "evaluated" && <p className="warn">{c.moves.reviseHelp}</p>}
          <ChangeEditor proposal={proposal} onChange={(next, problem) => setChange({ change: next, problem })} />
          {change.problem && <p className="muted">{change.problem}</p>}
          <NoteField value={note} onChange={setNote} />
        </>
      )}

      {move === "attach-evaluation" && <AttachField proposalId={proposal.id} value={seq} onChange={setSeq} />}

      {move === "approve" && (
        <>
          <p className="muted">{c.moves.approveRule}</p>
          <p>
            {c.detail.decision}: <DecisionBadge decision={decision} />
          </p>
          {decision === "fail" && (
            <p role="note" className="warn">
              {c.moves.failBlocked}
            </p>
          )}
          {decision === "insufficient" && (
            <label>
              {c.moves.acceptReason}
              <textarea value={accept} onChange={(e) => setAccept(e.target.value)} maxLength={1000} rows={3} />
            </label>
          )}
          {decision === "insufficient" && <p className="muted">{c.moves.acceptHelp}</p>}
          <NoteField value={note} onChange={setNote} />
        </>
      )}

      {(move === "reject" || move === "retire") && (
        <>
          <p className="warn">{move === "reject" ? c.moves.rejectWarn : c.moves.retireWarn}</p>
          <label>
            {c.moves.reason}
            <textarea value={text} onChange={(e) => setText(e.target.value)} maxLength={1000} rows={3} />
          </label>
        </>
      )}

      {move === "advance" && (
        <>
          <p>
            <strong>{c.moves.next(c.state[NEXT_STATE[proposal.state]] ?? NEXT_STATE[proposal.state])}</strong>
          </p>
          <label>
            {c.moves.evidence}
            <textarea value={text} onChange={(e) => setText(e.target.value)} maxLength={2000} rows={4} />
          </label>
        </>
      )}

      {!actor.trim() && <p className="muted">{c.actor.needed}</p>}
      {failed && (
        <p role="alert" className="error">
          {failed}
        </p>
      )}
      <button type="submit" disabled={busy || readOnly || !body}>
        {c.moves.submit}
      </button>{" "}
      <button type="button" onClick={onCancel}>
        {c.cancel}
      </button>
    </form>
  );
}

function NoteField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <label>
      {c.moves.note}
      <textarea value={value} onChange={(e) => onChange(e.target.value)} maxLength={1000} rows={2} />
    </label>
  );
}

function AttachField({ proposalId, value, onChange }: { proposalId: string; value: string; onChange: (v: string) => void }) {
  const { data, error, loading, reload } = useLoad(() => changesApi.evaluations(), [], c.loadFailed);
  if (error) return <ErrorState message={error} onRetry={reload} />;
  if (!data || loading) return <Loading />;
  const mine = data.entries.filter((entry) => entry.proposal_id === proposalId);
  if (mine.length === 0) return <p className="empty">{c.moves.ledgerNone}</p>;
  return (
    <label>
      {c.moves.ledgerPick}
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{c.moves.categoryPick}</option>
        {mine.map((entry) => (
          <option key={entry.seq} value={entry.seq}>
            {`#${entry.seq} ${entry.run_id ?? ""} (${c.decision[entry.decision ?? ""] ?? entry.decision ?? "—"})`}
          </option>
        ))}
      </select>
    </label>
  );
}

/** The change of a proposal: a stored profile version and/or knob values, or a text for the manual categories. */
function ChangeEditor({ proposal, onChange }: { proposal: Proposal; onChange: (change: ProposalChange | null, problem: string) => void }) {
  const category = proposal.category ?? "";
  const manual = MANUAL_CATEGORIES.includes(category);
  const knobsLoad = useLoad(() => (manual ? Promise.resolve({ knobs: [] as Knob[] }) : changesApi.knobs()), [manual], c.loadFailed);
  const versionsLoad = useLoad(
    () => (manual ? Promise.resolve(null) : changesApi.versions(proposal.profile_id).catch(() => null)),
    [manual, proposal.profile_id],
    c.loadFailed,
  );
  const [version, setVersion] = useState("");
  const [rows, setRows] = useState<Row[]>([]);
  const [manualText, setManualText] = useState("");

  const knobs = knobsLoad.data?.knobs ?? [];
  const checked = checkKnobRows(rows, knobs);

  function report(nextVersion: string, nextRows: Row[], nextManual: string) {
    if (manual) {
      onChange(nextManual.trim() ? { manual: nextManual.trim() } : null, c.moves.needManual);
      return;
    }
    const result = checkKnobRows(nextRows, knobs);
    const hasRows = nextRows.length > 0;
    const out: ProposalChange = {};
    if (nextVersion) out.profile_version = nextVersion;
    if (hasRows && result.change) out.knobs = result.change;
    let problem = "";
    if (hasRows && !result.change) problem = result.errors.find((e) => e) ?? "";
    else if (PROFILE_CATEGORIES.includes(category) && !nextVersion) problem = c.moves.needVersion;
    else if (KNOB_CATEGORIES.includes(category) && !hasRows) problem = c.moves.needKnobs;
    else if (!nextVersion && !hasRows) problem = c.moves.needChange;
    onChange(problem ? null : out, problem);
  }

  if (manual) {
    return (
      <label>
        {c.moves.manualLabel}
        <textarea
          value={manualText}
          maxLength={2000}
          rows={4}
          onChange={(e) => {
            setManualText(e.target.value);
            report(version, rows, e.target.value);
          }}
        />
      </label>
    );
  }
  if (knobsLoad.error) return <ErrorState message={knobsLoad.error} onRetry={knobsLoad.reload} />;
  if (knobsLoad.loading || versionsLoad.loading) return <Loading />;

  const versions = versionsLoad.data?.versions.filter((v) => !v.error) ?? [];
  function update(nextRows: Row[]) {
    setRows(nextRows);
    report(version, nextRows, manualText);
  }
  return (
    <div>
      <label>
        {c.moves.versionLabel}
        <select
          value={version}
          onChange={(e) => {
            setVersion(e.target.value);
            report(e.target.value, rows, manualText);
          }}
        >
          <option value="">{c.moves.versionNone}</option>
          {versions.map((v) => (
            <option key={v.version} value={v.version}>
              {v.version}
            </option>
          ))}
        </select>
      </label>
      {versions.length === 0 && <p className="muted">{c.moves.versionsMissing}</p>}

      <fieldset>
        <legend>{c.moves.knobsLabel}</legend>
        {rows.map((row, i) => {
          const knob = knobs.find((k) => k.name === row.name);
          const error = row.name && (row.value !== "" || checked.errors[i] === c.moves.knobDuplicate) ? checked.errors[i] : "";
          return (
            <div className="ch-knob-row" key={i}>
              <label>
                {`${c.moves.knobName} ${i + 1}`}
                <select value={row.name} onChange={(e) => update(rows.map((r, j) => (j === i ? { name: e.target.value, value: "" } : r)))}>
                  <option value="">{c.moves.knobPick}</option>
                  {knobs.map((k) => (
                    <option key={k.name} value={k.name}>
                      {k.risk === "high" ? `${k.name} (${c.moves.knobHigh})` : k.name}
                    </option>
                  ))}
                </select>
              </label>
              {knob && (
                <label>
                  {`${c.moves.knobValue} ${i + 1}`}
                  {knob.kind === "bool" ? (
                    <select value={row.value} onChange={(e) => update(rows.map((r, j) => (j === i ? { ...r, value: e.target.value } : r)))}>
                      <option value="">{c.moves.knobPick}</option>
                      <option value="true">{c.knobs.on}</option>
                      <option value="false">{c.knobs.off}</option>
                    </select>
                  ) : (
                    <input
                      type="number"
                      step={knob.kind === "int" ? 1 : "any"}
                      value={row.value}
                      onChange={(e) => update(rows.map((r, j) => (j === i ? { ...r, value: e.target.value } : r)))}
                    />
                  )}
                </label>
              )}
              {knob && (
                <span className="muted">
                  {knob.risk === "high" && <span className="badge failing">{c.moves.knobHigh}</span>} {knob.kind !== "bool" && c.moves.knobRange(knob.lo, knob.hi)} ({c.knobs.colValue}: {knobText(knob.value)})
                </span>
              )}
              {error && (
                <span role="alert" className="error">
                  {error}
                </span>
              )}
              <button type="button" onClick={() => update(rows.filter((_, j) => j !== i))}>
                {c.moves.knobRemove}
              </button>
            </div>
          );
        })}
        <button type="button" onClick={() => update([...rows, { name: "", value: "" }])}>
          {c.moves.knobAdd}
        </button>
      </fieldset>
    </div>
  );
}
