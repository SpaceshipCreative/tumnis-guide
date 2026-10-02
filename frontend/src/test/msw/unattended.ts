// MSW handlers for unattended runs (P4-04, FR-4.5): the window in force (`GET` and `PUT
// /v1/unattended/window`, the workspace's or one project's) and a task's queue flag (`PUT
// /v1/tasks/{id}/unattended`). Bodies are typed with the generated response types.
import { http, HttpResponse, type RequestHandler } from "msw";

import type {
  UnattendedOut,
  UnattendedWindowIn,
  UnattendedWindowOut,
  WindowSpec,
} from "../../api/types.gen";
import type { Recorder } from "./settings";

/** Monday to Friday, 22:00 to 06:00 (crossing midnight), as the API answers it. */
export const WEEKNIGHTS: WindowSpec = {
  weekdays: [0, 1, 2, 3, 4],
  start_local: "22:00:00",
  end_local: "06:00:00",
};

export function unattendedWindow(
  overrides: Partial<UnattendedWindowOut> = {},
): UnattendedWindowOut {
  return {
    project_id: null,
    window: null,
    source: "none",
    version: null,
    ...overrides,
  };
}

/** The default read: no window anywhere (what a new workspace answers). */
export const noUnattendedWindow: RequestHandler = http.get(
  "*/v1/unattended/window",
  ({ request }) =>
    HttpResponse.json(
      unattendedWindow({
        project_id: new URL(request.url).searchParams.get("project_id"),
      }),
    ),
);

/**
 * `GET` and `PUT /v1/unattended/window`. The read answers `loaded`; a save answers what
 * was sent (a window of its own, one version on), and `window: null` for a project falls
 * back to `fallback` (the workspace's window, or none).
 */
export function unattendedWindowHandlers(
  recorder: Recorder,
  loaded: UnattendedWindowOut = unattendedWindow(),
  fallback: WindowSpec | null = null,
): RequestHandler[] {
  return [
    http.get("*/v1/unattended/window", () => HttpResponse.json(loaded)),
    http.put("*/v1/unattended/window", async ({ request }) => {
      const sent = await recorder.record(request);
      const body = sent.body as UnattendedWindowIn;
      const projectId = body.project_id ?? null;
      if (body.window === null) {
        const inherited = projectId !== null && fallback !== null;
        return HttpResponse.json({
          project_id: projectId,
          window: inherited ? fallback : null,
          source: inherited ? "workspace" : "none",
          version: null,
        } satisfies UnattendedWindowOut);
      }
      return HttpResponse.json({
        project_id: projectId,
        window: body.window,
        source: projectId === null ? "workspace" : "project",
        version: (body.version ?? 0) + 1,
      } satisfies UnattendedWindowOut);
    }),
  ];
}

/** `PUT /v1/tasks/:taskId/unattended`: answers the flag it was sent. */
export function taskUnattendedHandlers(recorder: Recorder): RequestHandler[] {
  return [
    http.put("*/v1/tasks/:taskId/unattended", async ({ request, params }) => {
      const sent = await recorder.record(request);
      const queued = (sent.body as { queued: boolean }).queued;
      return HttpResponse.json({
        schema_version: 1,
        task_id: String(params.taskId),
        queued,
        queued_at: queued ? "2026-03-09T12:00:00Z" : null,
        queued_by: null,
        may_run_unattended: true,
      } satisfies UnattendedOut);
    }),
  ];
}
