import { useEffect, useRef } from "react";
import { jaExtraction as x } from "../../i18n/extraction";

interface Props {
  title: string;
  body: string;
  confirmLabel: string;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/** Asks "are you sure" before something that is hard to undo. The focus starts on Cancel; Esc cancels. */
export function ConfirmDialog({ title, body, confirmLabel, busy, onConfirm, onCancel }: Props) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  useEffect(() => cancelRef.current?.focus(), []);
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="extraction-dialog-title"
      aria-describedby="extraction-dialog-body"
      className="dialog"
      onKeyDown={(e) => {
        if (e.key === "Escape") onCancel();
      }}
    >
      <h2 id="extraction-dialog-title">{title}</h2>
      <p id="extraction-dialog-body">{body}</p>
      <button type="button" disabled={busy} onClick={onConfirm}>
        {confirmLabel}
      </button>{" "}
      <button type="button" ref={cancelRef} onClick={onCancel}>
        {x.cancel}
      </button>
    </div>
  );
}
