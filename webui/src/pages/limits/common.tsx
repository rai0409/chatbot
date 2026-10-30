import { ApiError } from "../../api/client";
import { jaLimits as j } from "../../i18n/limits";
import "./limits.css";

export const isForbidden = (e: unknown): boolean => e instanceof ApiError && e.status === 403;

/** The words for a failed change. The server's own text is kept for 400 (it says what to fix); 409 asks to reload. */
export function failureText(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 403) return j.forbidden;
    if (e.status === 409) return j.conflict;
    if (e.status === 400 || e.status === 404) return `${j.failed} ${e.detail}`;
  }
  return j.failed;
}

/** A number for people: thousands separators, at most two decimals (costs are estimates). */
export function amount(value: number): string {
  return value.toLocaleString("ja-JP", { maximumFractionDigits: 2 });
}

export function Notice({ text }: { text: string }) {
  return text ? (
    <p role="status" className="notice">
      {text}
    </p>
  ) : null;
}
