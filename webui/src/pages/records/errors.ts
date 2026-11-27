import { describeError } from "../../lib/errors";
import { jaRecords } from "../../i18n/records";

/** The words for a failed change: 403 and 409 have their own sentence; a 400 shows the server's message. */
export function failureText(e: unknown, fallback: string, forbidden: string = jaRecords.common.forbidden): string {
  return describeError(e, fallback, { 403: forbidden, 409: jaRecords.common.conflict });
}
