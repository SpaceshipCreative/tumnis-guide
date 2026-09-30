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
