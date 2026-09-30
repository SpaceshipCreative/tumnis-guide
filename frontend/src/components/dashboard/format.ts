// Dashboard wording for days, instants and durations (P0-23). The copy is English, so the
// formats are too; instants show in the workspace timezone, calendar days as they are.
const LOCALE = "en-US";

/** A calendar day (`YYYY-MM-DD`), e.g. "Mar 20": the same day in every timezone. */
export function formatDay(day: string): string {
  return new Intl.DateTimeFormat(LOCALE, {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${day}T00:00:00Z`));
}

/** An instant in `timeZone`, e.g. "Mar 9, 10:30 AM". */
export function formatInstant(iso: string, timeZone: string): string {
  return new Intl.DateTimeFormat(LOCALE, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone,
  }).format(new Date(iso));
}

/** Today's date in `timeZone`, e.g. "Monday, March 9". */
export function formatToday(now: Date, timeZone: string): string {
  return new Intl.DateTimeFormat(LOCALE, {
    weekday: "long",
    month: "long",
    day: "numeric",
    timeZone,
  }).format(now);
}

/** Whole minutes as "45 min", "1 h" or "1 h 30 min". */
export function formatMinutes(minutes: number): string {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours === 0) return `${String(rest)} min`;
  return rest === 0
    ? `${String(hours)} h`
    : `${String(hours)} h ${String(rest)} min`;
}

/** The calendar day `now` falls on in `timeZone`, as `YYYY-MM-DD`. */
export function localDay(now: Date, timeZone: string): string {
  return new Intl.DateTimeFormat("en-CA", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone,
  }).format(now);
}
