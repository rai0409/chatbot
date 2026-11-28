import { useId } from "react";
import { t } from "../i18n/ja";
import { useTenant } from "../lib/tenant";

/** The tenant to work on, in the top bar: a list when the credential's tenants are known, otherwise a field with suggestions. */
export function TenantPicker() {
  const { tenant, setTenant, candidates, restricted, listId } = useTenant();
  const id = useId();
  const helpId = `${id}-help`;
  return (
    <span className="tenant-picker">
      <label htmlFor={id}>{t.app.tenant}</label>
      {restricted && candidates.length > 0 ? (
        <select id={id} value={tenant} aria-describedby={helpId} onChange={(e) => setTenant(e.target.value)}>
          <option value="">{t.app.tenantNone}</option>
          {candidates.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      ) : (
        <input
          id={id}
          type="text"
          value={tenant}
          list={listId || undefined}
          maxLength={64}
          size={14}
          autoComplete="off"
          placeholder={t.app.tenantHint}
          aria-describedby={helpId}
          onChange={(e) => setTenant(e.target.value)}
        />
      )}
      <span id={helpId} hidden>
        {t.app.tenantHelp}
      </span>
    </span>
  );
}
