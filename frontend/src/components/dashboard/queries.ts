// The dashboard's reads (P0-23), shared by the route loader and the components so the
// prefetch fills exactly the queries the page then reads.
//
// Seam until P0-18's routes are in the generated client: the Today query and the review
// count are fetched here under the query keys the generated ops will have
// (`[{ _id: "tasksListTasks", query }]`, `[{ _id: "tasksGetReviewCount" }]`), so the live
// socket refreshes them once LIVE_MAP lists those ops (P0-18), and swapping in
// `tasksListTasksOptions({ query: TODAY_QUERY })` and `tasksGetReviewCountOptions()`
// changes nothing else.
import { queryOptions, type QueryClient } from "@tanstack/react-query";
import * as z from "zod";

import { projectsListProjectsOptions } from "../../api/@tanstack/react-query.gen";
import { apiUrl, onUnauthorized } from "../../lib/fetch";
import { workspaceQuery } from "../settings/queries";
import type { TodayTask } from "./types";

export { workspaceQuery };

/** Every project (the PRD expects 3 to 10), in board order; the project list's query. */
export const projectsQuery = () =>
  projectsListProjectsOptions({ query: { limit: 200 } });

/** `GET /v1/tasks?status=today&order=today&limit=5`: the Today panel (FR-1.2). */
export const TODAY_QUERY = {
  status: "today",
  order: "today",
  limit: 5,
} as const;

const TASK_STATUSES = [
  "backlog",
  "today",
  "in_progress",
  "waiting_on_human",
  "in_review",
  "done",
] as const;

const zTodayTask = z.object({
  id: z.string(),
  project_id: z.string(),
  title: z.string(),
  label: z
    .enum(["human", "ai", "hybrid"])
    .nullish()
    .transform((v) => v ?? null),
  status: z.enum(TASK_STATUSES),
  version: z.number().int(),
  estimate_minutes: z
    .number()
    .int()
    .nullish()
    .transform((v) => v ?? null),
  first_action: z
    .string()
    .nullish()
    .transform((v) => v ?? null),
});

const zTodayPage = z.object({
  items: z.array(zTodayTask),
  total: z.number().int().optional(),
});

export interface TodayPage {
  readonly items: readonly TodayTask[];
  /** Every Today task, not only the five shown ("+N more"). */
  readonly total: number;
}

async function getJson(path: string, signal: AbortSignal): Promise<unknown> {
  const response = await fetch(apiUrl(path), {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
    signal,
  });
  if (response.status === 401) onUnauthorized();
  if (!response.ok) {
    throw new Error(`GET /v1${path} failed (${String(response.status)})`);
  }
  return response.json();
}

export function todayQuery() {
  return queryOptions({
    queryKey: [{ _id: "tasksListTasks", query: TODAY_QUERY }] as const,
    queryFn: async ({ signal }): Promise<TodayPage> => {
      const search = new URLSearchParams({
        status: TODAY_QUERY.status,
        order: TODAY_QUERY.order,
        limit: String(TODAY_QUERY.limit),
      });
      const page = zTodayPage.parse(await getJson(`/tasks?${search}`, signal));
      return { items: page.items, total: page.total ?? page.items.length };
    },
  });
}

// The count as a bare number or `{count}`: whichever P0-18's route answers.
const zCount = z.union([
  z.number().int(),
  z.object({ count: z.number().int() }).transform((body) => body.count),
]);

export function reviewCountQuery() {
  return queryOptions({
    queryKey: [{ _id: "tasksGetReviewCount" }] as const,
    queryFn: async ({ signal }): Promise<number> =>
      zCount.parse(await getJson("/review/count", signal)),
  });
}

// Generated query keys start with `{ _id }`; writes that change tasks refresh these.
const TASK_READS = new Set([
  "tasksListTasks",
  "tasksGetReviewCount",
  "projectsListProjects",
]);

/** Marks stale every read a task write can change: Today, the count, the cards. */
export function invalidateTaskReads(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({
    predicate: (query) => {
      const head = query.queryKey[0] as { _id?: unknown } | undefined;
      return typeof head?._id === "string" && TASK_READS.has(head._id);
    },
  });
}
