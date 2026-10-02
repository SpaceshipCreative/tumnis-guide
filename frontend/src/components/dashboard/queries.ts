// The dashboard's reads (P0-23), shared by the route loader and the components so the
// prefetch fills exactly the queries the page then reads. All are generated ops (their
// responses validated by the generated zod schemas) listed in LIVE_MAP, so the live socket
// refreshes them.
import { queryOptions, type QueryClient } from "@tanstack/react-query";

import {
  agentsGetPausesOptions,
  coolifyListDeployStatusOptions,
  planningGetAlternatesOptions,
  planningGetPlanOptions,
  projectsListProjectsOptions,
  tasksGetReviewCountOptions,
  tasksListJustAddedOptions,
  tasksListTasksOptions,
} from "../../api/@tanstack/react-query.gen";
import { workspaceQuery } from "../settings/queries";

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

// One retry for network and server errors; a problem+json 4xx will not change on a retry.
function retryOnce(failureCount: number, error: unknown): boolean {
  const status =
    typeof error === "object" && error !== null && "status" in error
      ? error.status
      : undefined;
  return failureCount < 1 && !(typeof status === "number" && status < 500);
}

/** The Today page: at most five tasks in today order and `total` ("+N more"). */
export function todayQuery() {
  return queryOptions({
    ...tasksListTasksOptions({ query: TODAY_QUERY }),
    retry: retryOnce,
  });
}

/** Today's captures still in Backlog, newest first, at most three (A1.1, decision 83). */
export function justAddedQuery() {
  return queryOptions({
    ...tasksListJustAddedOptions(),
    retry: retryOnce,
  });
}

/** The day's published plan (P1-11); a 404 (no plan for the day) is not retried, and the
 * page does not ask again on mount after the loader's answer (the live socket refreshes it
 * when a plan is published), so a day without a plan shows its Today tasks at once. */
export function planQuery(day: string) {
  return queryOptions({
    ...planningGetPlanOptions({ path: { day } }),
    retry: retryOnce,
    retryOnMount: false,
  });
}

/** What a swap can bring in for the day (P1-11): read when the picker opens. */
export function alternatesQuery(day: string) {
  return queryOptions({
    ...planningGetAlternatesOptions({ path: { day } }),
    retry: retryOnce,
  });
}

/** The review badge: `{count}` of items waiting now. */
export function reviewCountQuery() {
  return queryOptions({
    ...tasksGetReviewCountOptions(),
    retry: retryOnce,
  });
}

/** Linked Coolify applications' last deploys and previews, per project (P2-14). */
export function deployStatusQuery() {
  return queryOptions({
    ...coolifyListDeployStatusOptions(),
    retry: retryOnce,
  });
}

/** What is paused now (P2-09): the kill switch in the header and the project pause. */
export function pausesQuery() {
  return queryOptions({
    ...agentsGetPausesOptions(),
    retry: retryOnce,
  });
}

// Generated query keys start with `{ _id }`; writes that change tasks refresh these.
const TASK_READS = new Set([
  "tasksListTasks",
  "tasksListJustAdded",
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
