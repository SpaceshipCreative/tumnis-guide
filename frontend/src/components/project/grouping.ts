/* eslint-disable @typescript-eslint/no-unused-vars -- a P0-24 spec stub */
// The Tasks view's grouping rule (P0-24, FR-2.6): Today, Up next, Waiting on you,
// Waiting on agent and Done recently. Pure: the time and the workspace timezone come in.
import type { TaskOut } from "../../api/types.gen";

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
  TaskOut,
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

/** Whole local calendar days in `tz` from the instant `fromIso` to `now`. */
export function localDaysBetween(
  _fromIso: string,
  _now: Date,
  _tz: string,
): number {
  throw new Error("not implemented: P0-24");
}

export function groupOf(_t: TaskLite, _now: Date, _tz: string): Group | null {
  throw new Error("not implemented: P0-24");
}

export function groupTasks<T extends TaskLite>(
  _tasks: readonly T[],
  _now: Date,
  _tz: string,
): Record<Group, T[]> {
  throw new Error("not implemented: P0-24");
}
