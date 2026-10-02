// The cached reads a task write can change (P0-24): task lists, boards, one task, the
// project cards and their counts, the review badge and the recurring tasks. Generated
// query keys start with `{ _id }` (R-19); the recurrence ones are this WP's hand queries
// until P0-19's ops are generated.
import type { QueryClient } from "@tanstack/react-query";

const TASK_VIEWS = new Set([
  "tasksListTasks",
  "tasksListJustAdded", // the dashboard's Just added list (A1.1)
  "tasksListTaskHistory", // the drawer's History (A1.1)
  "tasksGetBoard",
  "tasksGetTask",
  "tasksListComments",
  "tasksGetReviewCount",
  "projectsListProjects",
  "projectsGetProject",
  "tasksListRecurrence",
  "tasksGetRecurrence",
  "planningGetProjectWeek", // due dates and the tasks to schedule (P1-12)
]);

/** The `_id` of a generated (or hand-made, same-shaped) query key. */
export function queryId(queryKey: readonly unknown[]): string | undefined {
  const head = queryKey[0] as { _id?: unknown } | undefined;
  return typeof head?._id === "string" ? head._id : undefined;
}

/** Marks stale (and refetches, when shown) every read a task write can change. */
export function invalidateTaskViews(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({
    predicate: (query) => TASK_VIEWS.has(queryId(query.queryKey) ?? ""),
  });
}
