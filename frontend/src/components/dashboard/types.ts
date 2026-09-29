// What the dashboard reads of a project and of a Today task (P0-23). Structural subsets of
// the generated `ProjectOut` and of P0-18's `TaskOut`, so the generated types, the zod
// factories' output and the MSW fixtures all fit.
import type { Health } from "../../api/types.gen";

export type TaskLabel = "human" | "ai" | "hybrid";
export type TaskStatus =
  | "backlog"
  | "today"
  | "in_progress"
  | "waiting_on_human"
  | "in_review"
  | "done";

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

export interface TodayTask {
  readonly id: string;
  readonly project_id: string;
  readonly title: string;
  /** null: label pending (R-08). */
  readonly label: TaskLabel | null;
  readonly status: TaskStatus;
  readonly version: number;
  readonly estimate_minutes: number | null;
  readonly first_action: string | null;
}
