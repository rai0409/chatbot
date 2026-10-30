/** A time from the server (ISO 8601) as local date and time; an empty or unreadable value as a dash. */
export function dateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("ja-JP", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function percent(part: number, whole: number): string {
  return whole > 0 ? `${Math.round((part / whole) * 1000) / 10}%` : "—";
}

export function number(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : value.toLocaleString("ja-JP");
}
