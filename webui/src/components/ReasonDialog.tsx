import { useState } from "react";
import { t } from "../i18n/ja";

interface Props {
  title: string;
  /** The label of the text the person must write (a reason). */
  label?: string;
  help?: string;
  confirmLabel?: string;
  busy?: boolean;
  /** Needs a text before it can be confirmed (default: yes). */
  required?: boolean;
  onConfirm: (text: string) => void;
  onCancel: () => void;
}

/** A dialog that asks for a reason (or a confirmation) before something that changes the installation. */
export function ReasonDialog({ title, label = t.system.reasonLabel, help = t.system.reasonHelp, confirmLabel = t.system.confirm, busy, required = true, onConfirm, onCancel }: Props) {
  const [text, setText] = useState("");
  const ok = !required || text.trim().length > 0;
  return (
    <div role="dialog" aria-modal="true" aria-labelledby="dialog-title" className="dialog">
      <h2 id="dialog-title">{title}</h2>
      <label>
        {label}
        <textarea value={text} onChange={(e) => setText(e.target.value)} maxLength={300} rows={3} />
      </label>
      <p className="muted">{help}</p>
      <button type="button" disabled={busy || !ok} onClick={() => onConfirm(text.trim())}>
        {confirmLabel}
      </button>
      <button type="button" onClick={onCancel}>
        {t.system.cancel}
      </button>
    </div>
  );
}
