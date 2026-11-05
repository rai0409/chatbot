import { useRef, useState } from "react";
import { ApiError } from "../../api/client";
import { type ImportResult, approvedQaApi } from "../../api/approvedQa";
import { jaApprovedQa as q } from "../../i18n/approvedQa";
import { rememberActor, storedActor } from "../../lib/actor";

function toBase64(buffer: ArrayBuffer): string {
  let binary = "";
  const bytes = new Uint8Array(buffer);
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

/** Import the CSV of the Excel template: a check that writes nothing, then the import (drafts only). The file goes as bytes, so a Shift_JIS file keeps its encoding. */
export function ImportPanel({ tenantDefault, onImported, onClose }: { tenantDefault: string; onImported: () => void; onClose: () => void }) {
  const [tenant, setTenant] = useState(tenantDefault || "default");
  const [conflict, setConflict] = useState("skip");
  const [reason, setReason] = useState("");
  const [actor, setActor] = useState(storedActor);
  const [content, setContent] = useState("");
  const [fileName, setFileName] = useState("");
  const [result, setResult] = useState<ImportResult | null>(null);
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState(false);
  const picker = useRef<HTMLInputElement>(null);

  async function choose(file: File | undefined) {
    setResult(null);
    setProblem("");
    if (!file) return;
    setFileName(file.name);
    setContent(toBase64(await file.arrayBuffer()));
  }

  async function run(apply: boolean) {
    if (!content) return setProblem(q.importNeedFile);
    if (apply && (!reason.trim() || !actor.trim())) return setProblem(q.needReason);
    setBusy(true);
    setProblem("");
    try {
      if (apply) rememberActor(actor.trim());
      const res = await approvedQaApi.importCsv({
        tenant_id: tenant.trim() || "default",
        content_base64: content,
        apply,
        on_conflict: conflict,
        ...(apply ? { reason: reason.trim(), actor: actor.trim() } : {}),
      });
      setResult(res);
      if (apply) onImported();
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) setProblem(e instanceof ApiError && e.status === 403 ? q.forbidden : e instanceof ApiError && e.detail ? q.failedWith(e.detail) : q.failed);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel" aria-labelledby="qa-import-title">
      <h2 id="qa-import-title">{q.importTitle}</h2>
      <p className="muted">{q.importHelp}</p>
      <p>
        <a href={approvedQaApi.templateUrl}>{q.template}</a>
      </p>
      <div className="toolbar">
        <label>
          {q.importFile}
          <input ref={picker} type="file" accept=".csv,text/csv" onChange={(e) => void choose(e.target.files?.[0])} />
        </label>
        {fileName && <span className="muted">{fileName}</span>}
        <label>
          {q.importDefaultTenant}
          <input value={tenant} onChange={(e) => setTenant(e.target.value)} maxLength={64} />
        </label>
        <label>
          {q.importConflict}
          <select value={conflict} onChange={(e) => setConflict(e.target.value)}>
            <option value="skip">{q.importSkip}</option>
            <option value="update">{q.importUpdate}</option>
          </select>
        </label>
      </div>
      <div className="toolbar">
        <label>
          {q.reason}
          <input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300} size={40} />
        </label>
        <label>
          {q.actor}
          <input value={actor} onChange={(e) => setActor(e.target.value)} maxLength={64} />
        </label>
      </div>
      {problem && (
        <p role="alert" className="error">
          {problem}
        </p>
      )}
      <div className="toolbar">
        <button type="button" disabled={busy} onClick={() => void run(false)}>
          {q.importPreview}
        </button>
        <button type="button" disabled={busy || !result || result.applied || result.counts.create + result.counts.update === 0} onClick={() => void run(true)}>
          {q.importApply}
        </button>
        <button type="button" disabled={busy} onClick={onClose}>
          {q.close}
        </button>
      </div>
      {result && (
        <>
          <p role="status" className={result.applied ? "notice" : "muted"}>
            {result.applied ? q.importApplied : q.importPreviewed} {q.importCounts(result.counts)}
          </p>
          {result.rows.length > 0 && (
            <div style={{ overflowX: "auto" }}>
              <table>
                <thead>
                  <tr>
                    <th scope="col">{q.colLine}</th>
                    <th scope="col">{q.colQuestion}</th>
                    <th scope="col">{q.colResult}</th>
                    <th scope="col">{q.colMessage}</th>
                  </tr>
                </thead>
                <tbody>
                  {result.rows.map((r) => (
                    <tr key={r.line}>
                      <td>{r.line}</td>
                      <td>{r.question}</td>
                      <td className={r.action === "error" ? "error" : undefined}>{q.results[r.action] ?? r.action}</td>
                      <td>{r.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </section>
  );
}
