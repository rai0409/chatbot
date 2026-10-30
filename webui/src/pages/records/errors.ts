import { ApiError } from "../../api/client";
import { jaRecords } from "../../i18n/records";

/** The words for a failed change: 403 and 409 have their own sentence; a 400 shows the server's message. */
export function failureText(e: unknown, fallback: string, forbidden: string = jaRecords.common.forbidden): string {
  if (e instanceof ApiError) {
    if (e.status === 403) return forbidden;
    if (e.status === 409) return jaRecords.common.conflict;
    if (e.status === 400 && e.detail) return `${fallback}: ${e.detail}`;
  }
  return fallback;
}
