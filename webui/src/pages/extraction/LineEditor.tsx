import { useEffect, useRef, useState } from "react";
import type { PageLine } from "../../api/extraction";
import { jaExtraction as x } from "../../i18n/extraction";

interface Props {
  line: PageLine;
  busy: boolean;
  onSave: (text: string) => void;
  onConfirm: () => void;
  onRevert: () => void;
  onReingest: () => void;
  onClose: () => void;
}

/** Edit one line. Enter saves, Esc closes (the parent puts the focus back), Shift+Enter is a line break. */
export function LineEditor({ line, busy, onSave, onConfirm, onRevert, onReingest, onClose }: Props) {
  const [text, setText] = useState(line.text);
  const [problem, setProblem] = useState("");
  const area = useRef<HTMLTextAreaElement>(null);
  useEffect(() => area.current?.focus(), []);

  function save() {
    if (busy) return;
    const next = text.trim();
    if (!next) return setProblem(x.empty);
    if (next === line.text.trim()) return setProblem(x.unchanged);
    setProblem("");
    onSave(next);
  }

  return (
    <section className="panel" aria-labelledby="ex-editor-title">
      <h3 id="ex-editor-title">{x.editorTitle}</h3>
      <p className="muted">{x.ocrText(line.text)}</p>
      {line.state !== "open" && <p>{x.alreadyDecided(x.states[line.state] ?? line.state)}</p>}
      <label className="ex-field">
        {x.textLabel}
        <textarea
          ref={area}
          value={text}
          rows={3}
          maxLength={2000}
          aria-describedby="ex-editor-help"
          aria-invalid={problem ? true : undefined}
          onChange={(e) => {
            setText(e.target.value);
            setProblem("");
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.preventDefault();
              onClose();
            } else if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              save();
            }
          }}
        />
      </label>
      <p id="ex-editor-help" className="muted">
        {x.textHelp}
      </p>
      {problem && (
        <p role="alert" className="error">
          {problem}
        </p>
      )}
      <div className="toolbar">
        <button type="button" disabled={busy} onClick={save}>
          {x.save}
        </button>
        <button type="button" disabled={busy} onClick={onConfirm}>
          {x.confirm}
        </button>
        <button type="button" disabled={busy || line.state === "open"} onClick={onRevert}>
          {x.revert}
        </button>
        <button type="button" disabled={busy} onClick={onReingest}>
          {x.reingest}
        </button>
        <button type="button" onClick={onClose}>
          {x.cancelEdit}
        </button>
      </div>
    </section>
  );
}
