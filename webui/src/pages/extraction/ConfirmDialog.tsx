import { useId } from "react";
import { Dialog } from "../../components/Dialog";
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
  const bodyId = useId();
  return (
    <Dialog title={title} onCancel={onCancel} busy={busy} describedBy={bodyId}>
      <p id={bodyId}>{body}</p>
      <button type="button" className="danger" disabled={busy} onClick={onConfirm}>
        {confirmLabel}
      </button>{" "}
      <button type="button" data-autofocus onClick={onCancel}>
        {x.cancel}
      </button>
    </Dialog>
  );
}
