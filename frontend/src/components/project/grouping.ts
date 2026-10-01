// The Tasks view's grouping rule (P0-24, FR-2.6): Today, Up next, Waiting on you,
// Waiting on agent and Done recently. Pure: the time and the workspace timezone come in.
import type { Task } from "./types";

export type Group =
  "today" | "up_next" | "waiting_on_you" | "waiting_on_agent" | "done_recently";

export const GROUPS: readonly Group[] = [
  "today",
  "up_next",
  "waiting_on_you",
  "waiting_on_agent",
  "done_recently",
];

export const GROUP_LABELS: Record<Group, string> = {
  today: "Today",
  up_next: "Up next",
  waiting_on_you: "Waiting on you",
  waiting_on_agent: "Waiting on agent",
  done_recently: "Done recently",
};

export const DONE_RECENT_DAYS = 7; // (plan default)

export type TaskLite = Pick<
  Task,
  | "id"
  | "project_id"
  | "parent_id"
  | "title"
  | "status"
  | "label"
  | "priority"
  | "due_on"
  | "estimate_minutes"
  | "completed_at"
  | "version"
>;

const DAY_MS = 24 * 60 * 60 * 1000;
const formats = new Map<string, Intl.DateTimeFormat>();

/** The calendar day (`YYYY-MM-DD`) of an instant in `tz`. */
export function localDay(at: Date, tz: string): string {
  let format = formats.get(tz);
  if (!format) {
    format = new Intl.DateTimeFormat("en-CA", {
      timeZone: tz,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
    formats.set(tz, format);
  }
  return format.format(at);
}

function dayNumber(day: string): number {
  return Date.parse(`${day}T00:00:00Z`) / DAY_MS;
}

// The day of the last `now` asked about: grouping a list asks for the same `now` once per
// task, and formatting a date in a timezone is the costly part.
let lastToday: { at: number; tz: string; day: string } | null = null;

function todayOf(now: Date, tz: string): string {
  const at = now.getTime();
  if (lastToday?.at !== at || lastToday.tz !== tz) {
    lastToday = { at, tz, day: localDay(now, tz) };
  }
  return lastToday.day;
}

/** Whole local calendar days in `tz` from the instant `fromIso` to `now`. */
export function localDaysBetween(
  fromIso: string,
  now: Date,
  tz: string,
): number {
  return (
    dayNumber(todayOf(now, tz)) - dayNumber(localDay(new Date(fromIso), tz))
  );
}

// Local calendar days and elapsed time differ by less than a day for the day boundary
// plus at most 26 hours for an offset change (UTC-12 to UTC+14), so only an instant
// between these two marks needs the timezone (formatting dates is costly; PERF-2).
const SURELY_RECENT_MS = (DONE_RECENT_DAYS - 3) * DAY_MS;
const SURELY_OLD_MS = (DONE_RECENT_DAYS + 3) * DAY_MS;

/** Was a task completed at `completedIso` done within the last `DONE_RECENT_DAYS`
 * local days? The same answer as `localDaysBetween(...) < DONE_RECENT_DAYS`. */
export function doneRecently(
  completedIso: string,
  now: Date,
  tz: string,
): boolean {
  const elapsed = now.getTime() - Date.parse(completedIso);
  if (elapsed < SURELY_RECENT_MS) return true;
  if (elapsed > SURELY_OLD_MS) return false;
  return localDaysBetween(completedIso, now, tz) < DONE_RECENT_DAYS;
}

export function groupOf(t: TaskLite, now: Date, tz: string): Group | null {
  switch (t.status) {
    case "waiting_on_human":
    case "in_review":
      return "waiting_on_you";
    case "in_progress":
      return t.label === "ai" ? "waiting_on_agent" : "today";
    case "today":
      return "today";
    case "backlog":
      return "up_next";
    case "done":
      return t.completed_at !== null && doneRecently(t.completed_at, now, tz)
        ? "done_recently"
        : null;
  }
}

export function groupTasks<T extends TaskLite>(
  tasks: readonly T[],
  now: Date,
  tz: string,
): Record<Group, T[]> {
  const groups: Record<Group, T[]> = {
    today: [],
    up_next: [],
    waiting_on_you: [],
    waiting_on_agent: [],
    done_recently: [],
  };
  for (const task of tasks) {
    const group = groupOf(task, now, tz);
    if (group) groups[group].push(task);
  }
  return groups;
}
