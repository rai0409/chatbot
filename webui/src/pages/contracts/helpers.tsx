import { Link } from "react-router-dom";
import { jaContracts as c } from "../../i18n/contracts";
import { reasonText } from "../../i18n/ja";
import { describeError } from "../../lib/errors";

export const INTEGRATION_OFF = "integration_off:";

export interface Notice {
  kind: "ok" | "error";
  text: string;
}

/** The reason after `integration_off:` when an answer says the external integration is off, otherwise null. */
export function integrationOffReason(detail: string | undefined | null): string | null {
  return detail && detail.startsWith(INTEGRATION_OFF) ? detail.slice(INTEGRATION_OFF.length) : null;
}

/** A failure in words: 409 (changed by someone else), 403, 404, or the server's own message. */
export function failureText(prefix: string, e: unknown): string {
  return describeError(e, prefix, {
    409: (err) => c.conflict + (err.detail ? `（${err.detail}）` : ""),
    403: `${prefix}: ${c.forbidden}`,
    404: `${prefix}: ${c.notFound}`,
  });
}

export function daysText(left: number): string {
  return left < 0 ? c.daysOverdue(-left) : left === 0 ? c.today : c.daysLeft(left);
}

export function kindText(kind: string, label: string): string {
  return label || c.kinds[kind] || kind;
}

export function displayValue(value: string | number | boolean | null): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

/** A calm explanation that an external integration is off (not a failure), with the way to the system status. */
export function IntegrationOff({ text, reason }: { text: string; reason: string }) {
  return (
    <div role="note" className="panel contracts-off">
      <p>
        <b>{text}</b>
      </p>
      {reason && (
        <p>
          {c.integrationReason}: {reasonText(reason)}
        </p>
      )}
      <p className="muted">{c.integrationRest}</p>
      <p>
        <Link to="/system">{c.integrationLink}</Link>
      </p>
    </div>
  );
}
