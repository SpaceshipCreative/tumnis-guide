// The project page's reads (P0-24), shared by the route loader and the components so a
// prefetch fills exactly the queries the page reads. Generated ops are validated by the
// generated zod schemas and listed in LIVE_MAP. The recurrence reads are hand queries
// shaped like P0-19's routes (`GET /v1/recurrence?project_id=`, `GET
// /v1/tasks/{id}/recurrence`) until those ops are generated; a missing route reads as
// "nothing repeats".
import { queryOptions } from "@tanstack/react-query";

import {
  knowledgeGetBriefOptions,
  projectsGetProjectOptions,
  tasksGetBoardOptions,
  tasksListCommentsOptions,
  tasksListTasksOptions,
} from "../../api/@tanstack/react-query.gen";
import { apiUrl } from "../../lib/fetch";

export const TASK_PAGE_LIMIT = 200; // the API's largest page

export const projectQuery = (projectId: string) =>
  projectsGetProjectOptions({ path: { project_id: projectId } });

/** Every open task of the project, one page (a project holds far fewer than 200). */
export const projectTasksQuery = (projectId: string) =>
  tasksListTasksOptions({
    query: { project_id: projectId, limit: TASK_PAGE_LIMIT },
  });

export const boardQuery = (projectId: string) =>
  tasksGetBoardOptions({ path: { project_id: projectId } });

export const briefQuery = (projectId: string) =>
  knowledgeGetBriefOptions({ path: { project_id: projectId } });

export const commentsQuery = (taskId: string) =>
  tasksListCommentsOptions({ path: { task_id: taskId }, query: { limit: 50 } });

// --- Recurrence (P0-19's routes) -------------------------------------------------------

export type Preset = "daily" | "weekdays" | "weekly" | "monthly";

/** A task's repeat rule as P0-19 answers it (`due_time` "HH:MM" or "HH:MM:SS"). */
export interface RecurrenceRule {
  id: string;
  task_id: string;
  project_id?: string;
  title: string;
  preset: Preset | null;
  cron: string | null;
  weekday: number | null;
  month_day: number | null;
  due_time: string;
  latest_task_id?: string | null;
  latest_occurrence_on?: string | null;
  next_due_at: string | null;
  version: number;
}

async function readJson<T>(path: string, missing: T): Promise<T> {
  const response = await fetch(apiUrl(path), {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  if (response.status === 404) return missing;
  if (!response.ok) {
    throw new Error(`Request failed (${String(response.status)})`);
  }
  return (await response.json()) as T;
}

export const projectRecurrenceQuery = (projectId: string) =>
  queryOptions({
    queryKey: [
      { _id: "tasksListRecurrence", query: { project_id: projectId } },
    ] as const,
    queryFn: async () =>
      (
        await readJson<{ items: RecurrenceRule[] }>(
          `/recurrence?project_id=${encodeURIComponent(projectId)}`,
          { items: [] },
        )
      ).items,
  });

export const taskRecurrenceQuery = (taskId: string) =>
  queryOptions({
    queryKey: [
      { _id: "tasksGetRecurrence", path: { task_id: taskId } },
    ] as const,
    queryFn: () =>
      readJson<RecurrenceRule | null>(`/tasks/${taskId}/recurrence`, null),
  });
