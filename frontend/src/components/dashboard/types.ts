// What the dashboard reads of a project and of a Today task (P0-23). Structural subsets of
// the generated `ProjectOut` and `TaskOut`, so the generated types, the zod factories'
// output and the MSW fixtures all fit.
import type { Health, Label, Status, TaskOut } from "../../api/types.gen";

export type TaskLabel = Label;
export type TaskStatus = Status;

export interface DashboardProject {
  readonly id: string;
  readonly name: string;
  readonly health: Health;
  readonly next_milestone: string | null;
  readonly open_count: number;
  readonly last_agent_activity_at?: string | null | undefined;
  readonly sort_key: string;
  readonly status?: "active" | "on_hold" | "completed" | undefined;
  readonly archived_at: string | null;
}

/** A Today task; `label` null: pending (R-08). */
export type TodayTask = Pick<
  TaskOut,
  | "id"
  | "project_id"
  | "title"
  | "label"
  | "status"
  | "version"
  | "estimate_minutes"
  | "first_action"
>;
