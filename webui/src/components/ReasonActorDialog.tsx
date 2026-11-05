import { useState, type ReactNode } from "react";
import { rememberActor, storedActor } from "../lib/actor";

interface Props {
  title: string;
  body?: ReactNode;
  confirmLabel: string;
  cancelLabel: string;
  reasonLabel: string;
  actorLabel: string;
  needBoth: string;
  busy?: boolean;
  /** Only the name is needed (a confirmation that is not a change of data). */
  reasonOptional?: boolean;
  onConfirm: (reason: string, actor: string) => void;
  onCancel: () => void;
}

/** A confirmation that needs a reason and the name of the person (decisions that are kept in a history). */
export function ReasonActorDialog({ title, body, confirmLabel, cancelLabel, reasonLabel, actorLabel, needBoth, busy, reasonOptional, onConfirm, onCancel }: Props) {
  const [reason, setReason] = useState("");
  const [actor, setActor] = useState(storedActor);
  const [problem, setProblem] = useState("");

  function submit() {
    if ((!reasonOptional && !reason.trim()) || !actor.trim()) return setProblem(needBoth);
    rememberActor(actor.trim());
    onConfirm(reason.trim(), actor.trim());
  }

  return (
    <div role="dialog" aria-modal="true" aria-labelledby="rad-title" className="dialog">
      <h2 id="rad-title">{title}</h2>
      {body && <p>{body}</p>}
      {!reasonOptional && (
        <div>
          <label htmlFor="rad-reason">{reasonLabel}</label>
          <textarea id="rad-reason" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300} rows={2} />
        </div>
      )}
      <label>
        {actorLabel}
        <input value={actor} onChange={(e) => setActor(e.target.value)} maxLength={64} />
      </label>
      {problem && (
        <p role="alert" className="error">
          {problem}
        </p>
      )}
      <button type="button" disabled={busy} onClick={submit}>
        {confirmLabel}
      </button>
      <button type="button" disabled={busy} onClick={onCancel}>
        {cancelLabel}
      </button>
    </div>
  );
}
