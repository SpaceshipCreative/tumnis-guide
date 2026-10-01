// MSW handlers for the dashboard's reads (P0-23): the project list, the Today query
// (`GET /v1/tasks?status=today&order=today&limit=5`, a `TaskPage` with `total`), the review
// badge count (`ReviewCountOut`), the workspace settings (for the timezone) and today's
// calendar strip (P1-10). Bodies are
// typed with the generated response types.
import { http, HttpResponse, type RequestHandler } from "msw";

import type {
  AgentFeedOut,
  ProjectDeployStatus,
  ReviewCountOut,
  TaskOut,
  TaskPage,
} from "../../api/types.gen";
import type { makeProject } from "../factories";
import { dayCalendar, NO_WINDOW, noPlan } from "./planning";
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
  items: readonly TaskOut[],
  total: number = items.length,
  onRequest?: (query: URLSearchParams) => void,
): RequestHandler {
  return http.get("/v1/tasks", ({ request }) => {
    onRequest?.(new URL(request.url).searchParams);
    const page: TaskPage = { items: [...items], next_cursor: null, total };
    return HttpResponse.json(page);
  });
}

/** `GET /v1/review/count`: the review badge count (P0-18). */
export function reviewCount(count: number): RequestHandler {
  const body: ReviewCountOut = { count };
  return http.get("/v1/review/count", () => HttpResponse.json(body));
}

/** `GET /v1/settings/workspace` in this timezone. */
export function workspaceTimezone(timezone: string): RequestHandler {
  return http.get("/v1/settings/workspace", () =>
    HttpResponse.json(workspaceSettings({ timezone })),
  );
}

/** `GET /v1/coolify/status`: each project's linked Coolify applications (P2-14). */
export function deployStatus(
  entries: readonly ProjectDeployStatus[] = [],
): RequestHandler {
  return http.get("/v1/coolify/status", () => HttpResponse.json(entries));
}

/** `GET /v1/agents/feed`: the activity feed's agent runs (P2-17), empty by default. */
export function agentFeed(feed: Partial<AgentFeedOut> = {}): RequestHandler {
  const body: AgentFeedOut = {
    running: [],
    waiting: [],
    finished: [],
    failed: [],
    ...feed,
  };
  return http.get("/v1/agents/feed", () => HttpResponse.json(body));
}

/** The dashboard's reads, empty: no projects, nothing today, nothing to review, no apps,
 * no working hours or events today, no plan for the day (P1-11) and no agent runs in
 * the activity feed (P2-17). */
export const dashboardDefaults: RequestHandler[] = [
  agentFeed(),
  deployStatus(),
  projectsList([]),
  todayTasks([]),
  reviewCount(0),
  workspaceTimezone("America/New_York"),
  dayCalendar(NO_WINDOW),
  noPlan(),
];
