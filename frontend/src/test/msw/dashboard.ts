// MSW handlers for the dashboard's reads (P0-23): the project list, the Today query
// (`GET /v1/tasks?status=today&order=today&limit=5` with `total`), the review badge count
// and the workspace settings (for the timezone). The task and count shapes follow the
// planned P0-18 routes; once those are generated they follow `TaskOut` here, not in the
// tests.
import { http, HttpResponse, type RequestHandler } from "msw";

import type { makeProject, TaskStub } from "../factories";
import { workspaceSettings } from "./settings";

/** `GET /v1/projects` answering these projects on one page. */
export function projectsList(
  items: readonly ReturnType<typeof makeProject>[],
): RequestHandler {
  return http.get("/v1/projects", () =>
    HttpResponse.json({ items, next_cursor: null }),
  );
}

/**
 * `GET /v1/tasks` answering these tasks in this order, with `total` (defaults to the
 * number of items); `onRequest` sees each request's query string.
 */
export function todayTasks(
  items: readonly TaskStub[],
  total: number = items.length,
  onRequest?: (query: URLSearchParams) => void,
): RequestHandler {
  return http.get("/v1/tasks", ({ request }) => {
    onRequest?.(new URL(request.url).searchParams);
    return HttpResponse.json({ items, next_cursor: null, total });
  });
}

/** `GET /v1/review/count`: the review badge count (P0-18). */
export function reviewCount(count: number): RequestHandler {
  return http.get("/v1/review/count", () => HttpResponse.json({ count }));
}

/** `GET /v1/settings/workspace` in this timezone. */
export function workspaceTimezone(timezone: string): RequestHandler {
  return http.get("/v1/settings/workspace", () =>
    HttpResponse.json(workspaceSettings({ timezone })),
  );
}

/** The dashboard's reads, empty: no projects, nothing today, nothing to review. */
export const dashboardDefaults: RequestHandler[] = [
  projectsList([]),
  todayTasks([]),
  reviewCount(0),
  workspaceTimezone("America/New_York"),
];
