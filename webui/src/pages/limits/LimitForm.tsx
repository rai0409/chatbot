import { type FormEvent, useId, useState } from "react";
import { GLOBAL_ONLY, LIMIT_NAMES, limitsApi, type LimitChange, type LimitName } from "../../api/limits";
import { jaLimits as j } from "../../i18n/limits";
import { failureText, isForbidden } from "./common";

const f = j.limits.form;
const VALUE_RE = /^\d+(\.\d+)?$/;

interface Props {
  initialName: LimitName;
  /** The tenant the table is showing, as a starting point for the scope. */
  initialTenant: string;
  onDone: () => void;
  onCancel: () => void;
  /** The server says this administrator may not do it (403). */
  onForbidden: () => void;
}

/** The dialog that changes one limit. An empty value removes the setting; the reason is required. */
export function LimitForm({ initialName, initialTenant, onDone, onCancel, onForbidden }: Props) {
  const [name, setName] = useState<LimitName>(initialName);
  const [scope, setScope] = useState<"global" | "tenant">(initialTenant ? "tenant" : "global");
  const [tenant, setTenant] = useState(initialTenant);
  const [value, setValue] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const id = useId();
  const globalOnly = GLOBAL_ONLY.includes(name);
  const tenantScope = !globalOnly && scope === "tenant";

  async function submit(event: FormEvent) {
    event.preventDefault();
    const trimmed = value.trim();
    if (trimmed !== "" && !VALUE_RE.test(trimmed)) return setError(f.invalidValue);
    if (tenantScope && !tenant.trim()) return setError(f.tenantRequired);
    if (!reason.trim()) return setError(f.reasonRequired);
    const body: LimitChange = { name, value: trimmed === "" ? null : Number(trimmed), reason: reason.trim() };
    if (tenantScope) body.tenant_id = tenant.trim();
    setBusy(true);
    setError("");
    try {
      await limitsApi.setLimit(body);
      onDone();
    } catch (e) {
      if (isForbidden(e)) onForbidden();
      else setError(failureText(e));
      setBusy(false);
    }
  }

  return (
    <form role="dialog" aria-modal="true" aria-labelledby={`${id}-title`} className="dialog lm-form" onSubmit={submit} noValidate>
      <h2 id={`${id}-title`}>{f.title}</h2>
      <label>
        {f.name}
        <select value={name} onChange={(e) => setName(e.target.value as LimitName)}>
          {LIMIT_NAMES.map((n) => (
            <option key={n} value={n}>
              {j.limits.names[n]}
            </option>
          ))}
        </select>
      </label>
      <label>
        {f.scope}
        <select value={globalOnly ? "global" : scope} disabled={globalOnly} onChange={(e) => setScope(e.target.value as "global" | "tenant")}>
          <option value="global">{f.scopeGlobal}</option>
          <option value="tenant">{f.scopeTenant}</option>
        </select>
      </label>
      {globalOnly && <p className="muted">{f.globalOnlyHelp}</p>}
      <label>
        {f.tenantId}
        <input type="text" value={tenantScope ? tenant : ""} disabled={!tenantScope} maxLength={64} autoComplete="off" onChange={(e) => setTenant(e.target.value)} />
      </label>
      {!globalOnly && <p className="muted">{f.tenantIdHelp}</p>}
      <label>
        {f.value}
        <input type="text" inputMode="decimal" value={value} aria-describedby={`${id}-value`} autoComplete="off" onChange={(e) => setValue(e.target.value)} />
      </label>
      <p id={`${id}-value`} className="muted">
        {f.valueHelp}
      </p>
      <label>
        {j.reasonLabel}
        <textarea value={reason} rows={3} maxLength={300} onChange={(e) => setReason(e.target.value)} />
      </label>
      <p className="muted">{j.reasonHelp}</p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div className="lm-actions">
        <button type="submit" disabled={busy}>
          {busy ? j.busy : f.save}
        </button>
        <button type="button" onClick={onCancel}>
          {j.cancel}
        </button>
      </div>
    </form>
  );
}
