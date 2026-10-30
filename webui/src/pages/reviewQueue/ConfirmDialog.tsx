import { useEffect, useRef } from "react";

interface Props {
  title: string;
  body: string;
  confirmLabel: string;
  cancelLabel: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/** A dialog that asks "are you sure?" before a change that cannot be undone. */
export function ConfirmDialog({ title, body, confirmLabel, cancelLabel, busy, onConfirm, onCancel }: Props) {
  const first = useRef<HTMLButtonElement>(null);
  useEffect(() => first.current?.focus(), []);
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-title"
      className="dialog"
      onKeyDown={(e) => {
        if (e.key === "Escape") onCancel();
      }}
    >
      <h2 id="confirm-title">{title}</h2>
      <p>{body}</p>
      <button type="button" ref={first} disabled={busy} onClick={onConfirm}>
        {confirmLabel}
      </button>
      <button type="button" onClick={onCancel}>
        {cancelLabel}
      </button>
    </div>
  );
}
