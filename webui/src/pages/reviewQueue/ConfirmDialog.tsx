import { Dialog } from "../../components/Dialog";

interface Props {
  title: string;
  body: string;
  confirmLabel: string;
  cancelLabel: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/** A dialog that asks "are you sure?" before a change that cannot be undone. The focus starts on the safe button. */
export function ConfirmDialog({ title, body, confirmLabel, cancelLabel, busy, onConfirm, onCancel }: Props) {
  return (
    <Dialog title={title} onCancel={onCancel} busy={busy}>
      <p>{body}</p>
      <button type="button" className="danger" disabled={busy} onClick={onConfirm}>
        {confirmLabel}
      </button>
      <button type="button" data-autofocus onClick={onCancel}>
        {cancelLabel}
      </button>
    </Dialog>
  );
}
