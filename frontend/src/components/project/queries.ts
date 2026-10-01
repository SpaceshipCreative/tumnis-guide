// The project page's reads (P0-24), shared by the route loader and the components so a
// prefetch fills exactly the queries the page reads. Generated ops are validated by the
// generated zod schemas and listed in LIVE_MAP. The project's recurring tasks use the
// generated `tasksListRecurrence` (every page); the task's own rule is a hand query shaped
// like P0-19's `GET /v1/tasks/{id}/recurrence`. A missing route reads as "nothing repeats".
import { queryOptions } from "@tanstack/react-query";

import {
  knowledgeGetBriefOptions,
  projectsGetPolicyOptions,
  projectsGetProjectOptions,
  tasksGetBoardOptions,
  tasksListCommentsOptions,
  tasksListRecurrenceOptions,
  tasksListTasksOptions,
} from "../../api/@tanstack/react-query.gen";
import {
  tasksGetBoard,
  tasksListComments,
  tasksListRecurrence,
  tasksListTasks,
} from "../../api/sdk.gen";
import type {
  PageRecurrenceOut,
  RecurrenceOut,
  TasksListRecurrenceData,
} from "../../api/types.gen";
import { zBoardOut, zCardOut, zTaskOut, zTaskPage } from "../../api/zod.gen";
import { apiUrl } from "../../lib/fetch";
import { parseBoardInSlices, parsePageInSlices } from "../../lib/validate";

export const TASK_PAGE_LIMIT = 200; // the API's largest page

export const projectQuery = (projectId: string) =>
  projectsGetProjectOptions({ path: { project_id: projectId } });

/** The project's approval policy (FR-5.6), which the policy editor edits (P2-05). */
export const policyQuery = (projectId: string) =>
  projectsGetPolicyOptions({ path: { project_id: projectId } });

// The generated validators check a whole response in one task; these check the same
// schemas a slice of tasks or cards at a time (lib/validate.ts, PERF-2).
const validateTaskPage = (data: unknown) =>
  parsePageInSlices(zTaskPage, zTaskOut, data);
const validateBoard = (data: unknown) =>
  parseBoardInSlices(zBoardOut, zCardOut, data);

/**
 * Every task of the project: follows `next_cursor` through every page and answers one
 * `TaskPage` holding all of them (`next_cursor` null), under the generated op's key, so
 * LIVE_MAP and the optimistic writes treat it as the list they know.
 */
export const projectTasksQuery = (projectId: string) => {
  const query = { project_id: projectId, limit: TASK_PAGE_LIMIT };
  return queryOptions({
    ...tasksListTasksOptions({ query }),
    queryFn: async ({ signal }) => {
      const { data: first } = await tasksListTasks({
        query,
        signal,
        throwOnError: true,
        responseValidator: validateTaskPage,
      });
      const items = [...first.items];
      let cursor = first.next_cursor;
      while (cursor) {
        const { data: page } = await tasksListTasks({
          query: { ...query, cursor },
          signal,
          throwOnError: true,
          responseValidator: validateTaskPage,
        });
        items.push(...page.items);
        cursor = page.next_cursor;
      }
      return { ...first, items, next_cursor: null };
    },
  });
};

/** The board's layout, under the generated op's key (LIVE_MAP, the optimistic move). */
export const boardQuery = (projectId: string) =>
  queryOptions({
    ...tasksGetBoardOptions({ path: { project_id: projectId } }),
    queryFn: async ({ queryKey, signal }) => {
      const { data } = await tasksGetBoard({
        ...queryKey[0],
        signal,
        throwOnError: true,
        responseValidator: validateBoard,
      });
      return data;
    },
  });

export const briefQuery = (projectId: string) =>
  knowledgeGetBriefOptions({ path: { project_id: projectId } });

/**
 * Every comment of the task, oldest first: follows `next_cursor` like
 * `projectTasksQuery` and answers one page holding all of them, under the generated op's
 * key, so a new comment's invalidation refetches the whole list.
 */
export const commentsQuery = (taskId: string) => {
  const path = { task_id: taskId };
  const query = { limit: 50 };
  return queryOptions({
    ...tasksListCommentsOptions({ path, query }),
    queryFn: async ({ signal }) => {
      const { data: first } = await tasksListComments({
        path,
        query,
        signal,
        throwOnError: true,
      });
      const items = [...first.items];
      let cursor = first.next_cursor;
      while (cursor) {
        const { data: page } = await tasksListComments({
          path,
          query: { ...query, cursor },
          signal,
          throwOnError: true,
        });
        items.push(...page.items);
        cursor = page.next_cursor;
      }
      return { ...first, items, next_cursor: null };
    },
  });
};

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

/** One page of `GET /v1/recurrence`, or null when the route answers 404. */
async function recurrencePage(
  query: NonNullable<TasksListRecurrenceData["query"]>,
  signal: AbortSignal,
): Promise<PageRecurrenceOut | null> {
  const { data, response } = await tasksListRecurrence({ query, signal });
  if (data !== undefined) return data;
  if (response?.status === 404) return null;
  throw new Error(
    response ? `Request failed (${String(response.status)})` : "Request failed",
  );
}

/**
 * Every recurring task of the project (`GET /v1/recurrence?project_id=`): follows
 * `next_cursor` through every page like `projectTasksQuery`, under the generated op's
 * key. A 404 on the first page (the route is not there) reads as none.
 */
export const projectRecurrenceQuery = (projectId: string) => {
  const query = { project_id: projectId, limit: TASK_PAGE_LIMIT };
  return queryOptions({
    queryKey: tasksListRecurrenceOptions({ query }).queryKey,
    queryFn: async ({ signal }): Promise<RecurrenceOut[]> => {
      const first = await recurrencePage(query, signal);
      if (first === null) return [];
      const items = [...first.items];
      let cursor = first.next_cursor;
      while (cursor) {
        const { data: page } = await tasksListRecurrence({
          query: { ...query, cursor },
          signal,
          throwOnError: true,
        });
        items.push(...page.items);
        cursor = page.next_cursor;
      }
      return items;
    },
  });
};

export const taskRecurrenceQuery = (taskId: string) =>
  queryOptions({
    queryKey: [
      { _id: "tasksGetRecurrence", path: { task_id: taskId } },
    ] as const,
    queryFn: () =>
      readJson<RecurrenceRule | null>(`/tasks/${taskId}/recurrence`, null),
  });
