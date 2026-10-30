import { createContext, useCallback, useContext, useId, useState } from "react";
import { ApiError } from "../../api/client";
import { jaChanges as c } from "../../i18n/changes";

const ACTOR_KEY = "console.actorName";

function readActor(): string {
  try {
    return window.localStorage.getItem(ACTOR_KEY) ?? "";
  } catch {
    return "";
  }
}

function saveActor(name: string): void {
  try {
    window.localStorage.setItem(ACTOR_KEY, name);
  } catch {
    /* no storage: the page still works */
  }
}

/** The name of the person making a change, remembered in this browser. */
export function useActor(): [string, (name: string) => void] {
  const [actor, setActor] = useState(readActor);
  const set = useCallback((name: string) => {
    setActor(name);
    saveActor(name.trim());
  }, []);
  return [actor, set];
}

export function ActorField({ actor, onChange }: { actor: string; onChange: (name: string) => void }) {
  const helpId = useId();
  return (
    <div className="ch-actor">
      <label>
        {c.actor.label}
        <input type="text" value={actor} maxLength={64} aria-describedby={helpId} autoComplete="off" onChange={(e) => onChange(e.target.value)} />
      </label>
      <p id={helpId} className="muted">
        {c.actor.help}
      </p>
    </div>
  );
}

/** Set once the server says "not allowed" (403): the screen stays readable and the buttons that change things are off. */
export const ReadOnlyContext = createContext<{ readOnly: boolean; markReadOnly: () => void }>({ readOnly: false, markReadOnly: () => undefined });
export const useReadOnly = () => useContext(ReadOnlyContext);

/** The words for a failed change. The server's own message is kept for 400 and 409, which say what to fix. */
export function failureText(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 403) return c.readOnly;
    if (e.status === 409) return `${c.conflict} (${e.detail})`;
    if (e.status === 400 || e.status === 404) return `${c.failed} ${e.detail}`;
  }
  return c.failed;
}

export const isForbidden = (e: unknown): boolean => e instanceof ApiError && e.status === 403;

export function ReadOnlyNote() {
  const { readOnly } = useReadOnly();
  return readOnly ? (
    <p role="note" className="warn">
      {c.readOnly}
    </p>
  ) : null;
}

export function StateBadge({ state }: { state: string }) {
  const kind = state === "rejected" ? "failing" : state === "retired" ? "disabled" : ["approved", "live", "monitoring"].includes(state) ? "ok" : "not_configured";
  return <span className={`badge ${kind}`}>{c.state[state] ?? state}</span>;
}

export function DecisionBadge({ decision }: { decision: string | null | undefined }) {
  if (!decision) return <span className="muted">—</span>;
  const kind = decision === "pass" ? "ok" : decision === "fail" ? "failing" : "not_configured";
  return <span className={`badge ${kind}`}>{c.decision[decision] ?? decision}</span>;
}

export function shortDigest(value: string | null | undefined): string {
  return value ? value.slice(0, 12) : "—";
}

export function knobText(value: unknown): string {
  if (value === true) return c.knobs.on;
  if (value === false) return c.knobs.off;
  return value === null || value === undefined ? c.knobs.unset : String(value);
}
