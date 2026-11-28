// A failed request in words an administrator can act on. The API client (api/client.ts) turns every failure into an
// ApiError (a failure to reach the server is status 0); this file decides what to say for it.
import { ApiError } from "../api/client";
import { t } from "../i18n/ja";

interface ValidationItem {
  type?: string;
  loc?: unknown[];
  msg?: string;
  ctx?: Record<string, unknown>;
}

function isValidationList(detail: unknown): detail is ValidationItem[] {
  return Array.isArray(detail) && detail.length > 0 && detail.every((d) => d && typeof d === "object" && ("msg" in d || "type" in d));
}

/** The field a FastAPI validation error points at: the last named part of `loc` ("body" and list positions are skipped). */
function fieldName(loc: unknown[] | undefined): string {
  const named = (loc ?? []).filter((part): part is string => typeof part === "string" && part !== "body" && part !== "query" && part !== "path");
  return named.length > 0 ? named[named.length - 1] : "";
}

/** One validation problem in Japanese: the known pydantic types have a fixed sentence, anything else keeps the server's text. */
function problemText(item: ValidationItem): string {
  const f = t.errors.field;
  const type = item.type ?? "";
  const ctx = item.ctx ?? {};
  if (type === "missing") return f.missing;
  if (type === "string_too_long" || type === "too_long") return f.tooLong(Number(ctx.max_length) || undefined);
  if (type === "string_too_short" || type === "too_short") return f.tooShort(Number(ctx.min_length) || undefined);
  if (type === "int_parsing" || type === "int_from_float" || type === "int_type") return f.integer;
  if (type === "float_parsing" || type === "float_type" || type === "decimal_parsing") return f.number;
  if (type === "bool_parsing" || type === "bool_type") return f.boolean;
  if (type === "string_pattern_mismatch") return f.pattern;
  if (type === "literal_error" || type === "enum") return f.choice;
  if (type === "greater_than_equal" || type === "greater_than") return f.min(ctx.ge ?? ctx.gt);
  if (type === "less_than_equal" || type === "less_than") return f.max(ctx.le ?? ctx.lt);
  if (type === "json_invalid") return f.json;
  if (type === "string_type") return f.string;
  if (type === "list_type") return f.list;
  if (type === "value_error" && item.msg) return item.msg.replace(/^Value error,\s*/, "");
  return item.msg || f.invalid;
}

/** The `detail` of an error answer as one readable line: a string as it is, a validation list as "field: problem / …". */
export function formatDetail(detail: unknown): string {
  if (detail === undefined || detail === null) return "";
  if (typeof detail === "string") return detail;
  if (isValidationList(detail)) {
    return detail
      .map((item) => {
        const field = fieldName(item.loc);
        return field ? `${field}: ${problemText(item)}` : problemText(item);
      })
      .join(" / ");
  }
  if (typeof detail === "object") {
    const o = detail as Record<string, unknown>;
    for (const key of ["message", "reason", "error", "code"]) if (typeof o[key] === "string") return o[key] as string;
  }
  try {
    return JSON.stringify(detail);
  } catch {
    return String(detail);
  }
}

export type ErrorOverrides = Partial<Record<number, string | ((e: ApiError) => string)>>;

/** True when the request did not reach the server (offline, DNS, CORS, a dropped connection). */
export function isNetworkError(e: unknown): boolean {
  return (e instanceof ApiError && e.status === 0) || e instanceof TypeError;
}

/**
 * The words for a failure. `fallback` names what failed (e.g. 「保存できませんでした」); `overrides` lets a screen keep its
 * own sentence for a status. A 400/422 adds what the server found wrong; the other statuses have a fixed sentence.
 */
export function describeError(e: unknown, fallback: string, overrides: ErrorOverrides = {}): string {
  if (!(e instanceof ApiError)) return isNetworkError(e) ? t.errors.network : fallback;
  const own = overrides[e.status];
  if (own !== undefined) return typeof own === "function" ? own(e) : own;
  const s = e.status;
  if (s === 0) return t.errors.network;
  if (s === 400 || s === 422) return e.detail ? `${fallback}: ${e.detail}` : fallback;
  if (s === 401) return t.errors.unauthorized;
  if (s === 403) return t.errors.forbidden;
  if (s === 404) return t.errors.notFound;
  if (s === 409) return t.errors.conflict;
  if (s === 413) return t.errors.tooLarge;
  if (s === 429) return t.errors.tooMany;
  if (s >= 500) return `${fallback}: ${t.errors.server}`;
  return e.detail ? `${fallback}: ${e.detail}` : fallback;
}
