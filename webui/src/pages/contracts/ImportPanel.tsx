import { useState } from "react";
import { contractsApi, type ImportResult } from "../../api/contracts";
import { jaContracts as c } from "../../i18n/contracts";
import { failureText, type Notice } from "./helpers";

interface Props {
  tenant: string;
  onNotice: (n: Notice) => void;
  onImported: () => void;
}

/** Import the output of the contract extraction: every value is stored as "unconfirmed". */
export function ImportPanel({ tenant, onNotice, onImported }: Props) {
  const [id, setId] = useState("");
  const [doc, setDoc] = useState("");
  const [json, setJson] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ImportResult | null>(null);

  async function submit() {
    setResult(null);
    if (!id.trim()) return onNotice({ kind: "error", text: c.importNeedId });
    if (!json.trim()) return onNotice({ kind: "error", text: c.importNeedJson });
    let extracted: unknown;
    try {
      extracted = JSON.parse(json);
    } catch {
      return onNotice({ kind: "error", text: c.importBadJson });
    }
    setBusy(true);
    try {
      const res = await contractsApi.importExtraction(tenant, id.trim(), doc.trim(), extracted);
      setResult(res);
      onNotice({ kind: "ok", text: c.importDone(Object.keys(res.rejected).length) });
      onImported();
    } catch (e) {
      onNotice({ kind: "error", text: failureText(c.importFailed, e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="panel contracts-form">
      <summary>{c.importTitle}</summary>
      <label>
        {c.importId}
        <input value={id} onChange={(e) => setId(e.target.value)} maxLength={64} placeholder="service-2026-01" autoComplete="off" />
        <span className="muted">{c.importIdHint}</span>
      </label>
      <label>
        {c.importDoc}
        <input value={doc} onChange={(e) => setDoc(e.target.value)} maxLength={512} autoComplete="off" />
      </label>
      <label>
        {c.importJson}
        <textarea className="contracts-json" value={json} onChange={(e) => setJson(e.target.value)} spellCheck={false} />
        <span className="muted">{c.importJsonHint}</span>
      </label>
      <button type="button" disabled={busy} onClick={() => void submit()}>
        {busy ? c.working : c.importButton}
      </button>
      {result && Object.keys(result.rejected).length > 0 && (
        <div>
          <h3>{c.importRejected}</h3>
          <ul>
            {Object.entries(result.rejected).map(([name, why]) => (
              <li key={name}>
                {c.fieldNames[name] ?? name}: {why}
              </li>
            ))}
          </ul>
        </div>
      )}
      {result && result.kept.length > 0 && (
        <p className="muted">
          {c.importKept}: {result.kept.map((n) => c.fieldNames[n] ?? n).join("、")}
        </p>
      )}
    </details>
  );
}
