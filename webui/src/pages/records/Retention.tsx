import { useState } from "react";
import { recordsApi, type PurgeResult } from "../../api/records";
import { jaRecords } from "../../i18n/records";
import { number } from "../../lib/format";
import { failureText } from "./errors";

const r = jaRecords.retention;

function Result({ title, result }: { title: string; result: PurgeResult }) {
  const usage = typeof result.llm_usage?.files_deleted === "number" ? result.llm_usage.files_deleted : 0;
  return (
    <section className="panel rec-result" aria-label={title}>
      <h3 style={{ marginTop: 0 }}>{title}</h3>
      <p className="muted">
        {r.action}: {r.actionNames[result.records.action] ?? result.records.action}
      </p>
      <dl>
        <dt>{r.anonymized}</dt>
        <dd>{number(result.records.records_anonymized)}</dd>
        <dt>{r.device}</dt>
        <dd>{number(result.records.device_info_removed)}</dd>
        <dt>{r.files}</dt>
        <dd>{number(result.records.files_deleted)}</dd>
        <dt>{r.usageFiles}</dt>
        <dd>{number(usage)}</dd>
      </dl>
    </section>
  );
}

export function Retention() {
  const [action, setAction] = useState<"anonymize" | "delete">("anonymize");
  const [busy, setBusy] = useState(false);
  const [dry, setDry] = useState<PurgeResult | null>(null);
  const [real, setReal] = useState<PurgeResult | null>(null);
  const [problem, setProblem] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [understood, setUnderstood] = useState(false);

  async function run(dryRun: boolean) {
    setBusy(true);
    setProblem("");
    try {
      const result = await recordsApi.purge({ dry_run: dryRun, action });
      if (dryRun) setDry(result);
      else {
        setReal(result);
        setDry(null);
      }
      setConfirming(false);
      setUnderstood(false);
    } catch (e) {
      setProblem(failureText(e, r.failed, r.forbidden));
      setConfirming(false);
      setUnderstood(false);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <p>{r.intro}</p>
      <ul>
        {r.rules.map((rule) => (
          <li key={rule}>{rule}</li>
        ))}
      </ul>
      <p className="muted">{r.envNote}</p>
      <p className="muted">{r.onlyGlobal}</p>

      <fieldset>
        <legend>{r.action}</legend>
        {(["anonymize", "delete"] as const).map((item) => (
          <div key={item}>
            <label>
              <input type="radio" name="purge-action" checked={action === item} onChange={() => setAction(item)} /> {r[item]}
            </label>
          </div>
        ))}
      </fieldset>

      <p>
        <button type="button" disabled={busy} onClick={() => void run(true)}>
          {busy && !confirming ? r.dryRunning : r.dryRun}
        </button>{" "}
        <button type="button" disabled={busy} onClick={() => setConfirming(true)}>
          {r.run}
        </button>
      </p>

      {problem && (
        <p role="alert" className="error">
          {problem}
        </p>
      )}
      {dry && <Result title={r.dryResult} result={dry} />}
      {real && (
        <>
          <p role="status" className="notice">
            {r.done}
          </p>
          <Result title={r.realResult} result={real} />
        </>
      )}

      {confirming && (
        <form
          role="dialog"
          aria-modal="true"
          aria-labelledby="purge-title"
          className="dialog"
          onSubmit={(e) => {
            e.preventDefault();
            if (understood) void run(false);
          }}
        >
          <h2 id="purge-title">{r.confirmTitle}</h2>
          <p>{r.confirmBody}</p>
          <p>
            <strong>{r.confirmAction(r.actionNames[action])}</strong>
          </p>
          <label>
            <input type="checkbox" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} /> {r.confirmUndo}
          </label>
          <p>
            <button type="submit" disabled={busy || !understood}>
              {r.confirmRun}
            </button>{" "}
            <button type="button" onClick={() => setConfirming(false)}>
              {jaRecords.common.cancel}
            </button>
          </p>
        </form>
      )}
    </div>
  );
}
