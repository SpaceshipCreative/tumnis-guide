// Time arithmetic for views that place work in free time (P1-12's Calendar view; P1-11's
// fit offers reuse the slot helpers). Instants are ISO strings in UTC as the API sends
// them; wall-clock text is always in the workspace timezone the server names.

export interface Span {
  start: string;
  end: string;
}

/** Scheduling works on 15-minute marks. Every zone's offset is a multiple of 15 minutes,
 * so marks on the UTC clock are marks on the local clock too. */
export const SLOT_MINUTES = 15;
const MINUTE = 60_000;

/** "HH:MM" (24-hour) of an instant in `timeZone`. */
export function clockTime(at: string | Date, timeZone: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone,
  }).format(typeof at === "string" ? new Date(at) : at);
}

/** Minutes after local midnight of an instant in `timeZone`. */
export function minuteOfDay(at: string, timeZone: string): number {
  const [h, m] = clockTime(at, timeZone).split(":").map(Number) as [
    number,
    number,
  ];
  return h * 60 + m;
}

/** "HH:MM to HH:MM" of a span in `timeZone`. */
export function spanText(span: Span, timeZone: string): string {
  return `${clockTime(span.start, timeZone)} to ${clockTime(span.end, timeZone)}`;
}

const WEEKDAYS = [
  "Sunday",
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
];
const MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

function utcDate(day: string): Date {
  return new Date(`${day}T00:00:00Z`);
}

/** "Tuesday 10 March" for a local date `YYYY-MM-DD`. */
export function dayName(day: string): string {
  const d = utcDate(day);
  return `${WEEKDAYS[d.getUTCDay()] ?? ""} ${String(d.getUTCDate())} ${MONTHS[d.getUTCMonth()] ?? ""}`;
}

/** "10 March" for a local date `YYYY-MM-DD`. */
export function shortDate(day: string): string {
  const d = utcDate(day);
  return `${String(d.getUTCDate())} ${MONTHS[d.getUTCMonth()] ?? ""}`;
}

/** The local date `n` days after `day` (both `YYYY-MM-DD`). */
export function addDays(day: string, n: number): string {
  const d = utcDate(day);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** The local date of `now` in `timeZone`, `YYYY-MM-DD`. */
export function localDay(now: Date, timeZone: string): string {
  // en-CA formats dates as YYYY-MM-DD.
  return new Intl.DateTimeFormat("en-CA", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone,
  }).format(now);
}

/** Is `day` (`YYYY-MM-DD`) a Monday? */
export function isMonday(day: string): boolean {
  const d = utcDate(day);
  return !Number.isNaN(d.getTime()) && d.getUTCDay() === 1;
}

/** The Monday of the local week holding `now` in `timeZone`. */
export function mondayOf(now: Date, timeZone: string): string {
  const today = localDay(now, timeZone);
  const weekday = utcDate(today).getUTCDay(); // 0 = Sunday
  return addDays(today, -((weekday + 6) % 7));
}

/** `spans` minus every `taken` span (both sorted or not); what is left, by start. */
export function subtract(
  spans: readonly Span[],
  taken: readonly Span[],
): Span[] {
  const cuts = [...taken].sort(
    (a, b) => Date.parse(a.start) - Date.parse(b.start),
  );
  const left: Span[] = [];
  for (const span of spans) {
    let cursor = Date.parse(span.start);
    const end = Date.parse(span.end);
    for (const cut of cuts) {
      const from = Date.parse(cut.start);
      const to = Date.parse(cut.end);
      if (to <= cursor || from >= end) continue;
      if (from > cursor) {
        left.push({ start: iso(cursor), end: iso(from) });
      }
      cursor = Math.max(cursor, to);
    }
    if (cursor < end) left.push({ start: iso(cursor), end: iso(end) });
  }
  return left.sort((a, b) => Date.parse(a.start) - Date.parse(b.start));
}

function iso(ms: number): string {
  return new Date(ms).toISOString();
}

/** The 15-minute marks inside `span` (a slot starts there and lies inside the span),
 * none before `notBefore`. */
export function slotStarts(span: Span, notBefore?: Date): string[] {
  const step = SLOT_MINUTES * MINUTE;
  const floor = Math.max(Date.parse(span.start), notBefore?.getTime() ?? 0);
  const end = Date.parse(span.end);
  const starts: string[] = [];
  for (let at = Math.ceil(floor / step) * step; at + step <= end; at += step) {
    starts.push(iso(at));
  }
  return starts;
}

/** The span of `minutes` from `start`. */
export function blockFrom(start: string, minutes: number): Span {
  return {
    start: iso(Date.parse(start)),
    end: iso(Date.parse(start) + minutes * MINUTE),
  };
}

/** Does `block` lie inside one of `spans`? */
export function fitsIn(block: Span, spans: readonly Span[]): boolean {
  const from = Date.parse(block.start);
  const to = Date.parse(block.end);
  return spans.some(
    (s) => Date.parse(s.start) <= from && to <= Date.parse(s.end),
  );
}

/** The starts on 15-minute marks where `minutes` fit inside one of `spans`, none before
 * `notBefore`: a slot picker's choices. */
export function fittingStarts(
  spans: readonly Span[],
  minutes: number,
  notBefore?: Date,
): string[] {
  return spans.flatMap((span) =>
    slotStarts(span, notBefore).filter((start) =>
      fitsIn(blockFrom(start, minutes), [span]),
    ),
  );
}
