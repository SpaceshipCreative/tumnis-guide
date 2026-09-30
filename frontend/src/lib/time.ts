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

// Snooze ends for the review queue (P1-13, R-04): an hour, three hours, or the start of
// the next working day in the workspace timezone. Working days follow planning's rule
// (P1-10, FR-4.7): hours are local wall times per weekday (0 = Monday), a weekday without
// its own hours starts at 09:00, and Saturday and Sunday are skipped. Pure: `now`, the
// hours and the zone are passed in.

export type SnoozeChoice = "1h" | "3h" | "tomorrow";

export interface WorkingDayHours {
  weekday: number; // 0 = Monday
  start: string; // "HH:MM", local wall time
}

const HOUR_MS = 60 * 60_000;
const DEFAULT_START = "09:00";
const WEEKEND = new Set([5, 6]);
const LOOKAHEAD_DAYS = 7;

interface LocalParts {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
  second: number;
}

function partsIn(instant: Date, timeZone: string): LocalParts {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(instant);
  const value = (type: Intl.DateTimeFormatPartTypes) =>
    Number(parts.find((part) => part.type === type)?.value ?? 0);
  return {
    year: value("year"),
    month: value("month"),
    day: value("day"),
    hour: value("hour"),
    minute: value("minute"),
    second: value("second"),
  };
}

/** How far `timeZone`'s wall clock is ahead of UTC at `instant`, in milliseconds. */
function offsetMs(instant: Date, timeZone: string): number {
  const p = partsIn(instant, timeZone);
  const wall = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
  return wall - Math.floor(instant.getTime() / 1000) * 1000;
}

/**
 * The instant a local wall time names in `timeZone`. A time a DST gap skips moves forward
 * by the gap; an ambiguous one takes its first occurrence (as `core.clock.local_to_utc`).
 */
export function localToUtc(
  date: { year: number; month: number; day: number },
  time: string,
  timeZone: string,
): Date {
  const [hour = 0, minute = 0] = time.split(":").map(Number);
  const wall = Date.UTC(date.year, date.month - 1, date.day, hour, minute);
  // Every zone's offset is within 14 h, so these bracket the wall time's instant.
  const before = offsetMs(new Date(wall - 14 * HOUR_MS), timeZone);
  const after = offsetMs(new Date(wall + 14 * HOUR_MS), timeZone);
  for (const offset of [before, after]) {
    if (offsetMs(new Date(wall - offset), timeZone) === offset) {
      return new Date(wall - offset);
    }
  }
  return new Date(wall - before); // in a DST gap: forward by the gap
}

/** Monday = 0, as the working hours count weekdays. */
function weekdayOf(year: number, month: number, day: number): number {
  return (new Date(Date.UTC(year, month - 1, day)).getUTCDay() + 6) % 7;
}

/** The start of the first working day after today (in `timeZone`). */
export function nextWorkingDayStart(
  now: Date,
  days: readonly WorkingDayHours[],
  timeZone: string,
): Date {
  const today = partsIn(now, timeZone);
  const starts = new Map(days.map((row) => [row.weekday, row.start]));
  for (let ahead = 1; ahead <= LOOKAHEAD_DAYS; ahead++) {
    const date = new Date(
      Date.UTC(today.year, today.month - 1, today.day + ahead),
    );
    const local = {
      year: date.getUTCFullYear(),
      month: date.getUTCMonth() + 1,
      day: date.getUTCDate(),
    };
    const weekday = weekdayOf(local.year, local.month, local.day);
    if (WEEKEND.has(weekday)) continue;
    return localToUtc(local, starts.get(weekday) ?? DEFAULT_START, timeZone);
  }
  return new Date(now.getTime() + 24 * HOUR_MS); // unreachable: a week holds a weekday
}

/** When a snooze chosen at `now` ends. */
export function snoozeUntil(
  choice: SnoozeChoice,
  now: Date,
  days: readonly WorkingDayHours[],
  timeZone: string,
): Date {
  if (choice === "1h") return new Date(now.getTime() + HOUR_MS);
  if (choice === "3h") return new Date(now.getTime() + 3 * HOUR_MS);
  return nextWorkingDayStart(now, days, timeZone);
}
