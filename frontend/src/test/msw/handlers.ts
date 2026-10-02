import { http, HttpResponse, type RequestHandler } from "msw";

import type { PageReviewItemOut, PolicyOut } from "../../api/types.gen";
import { appOpen } from "./closeDay";
import { dashboardDefaults } from "./dashboard";
import { focusDefaults } from "./focus";
import { workingHours } from "./planning";
import { noUnattendedWindow } from "./unattended";

/**
 * `GET /v1/review/kinds` answering these registered kinds (the P0-18 registry). The
 * response shape lives here, not in the tests, so it can follow the generated schema.
 */
export function reviewKinds(kinds: readonly string[]): RequestHandler {
  return http.get("/v1/review/kinds", () =>
    HttpResponse.json({
      items: kinds.map((kind) => ({
        kind,
        owner_module: "test",
        actions: ["accept", "reject"],
        impact_scope: "workspace",
        payload_schema: {},
      })),
    }),
  );
}

/** The session probe (`GET /v1/auth/sessions?limit=1`, P0-13): signed in or not. */
export function session(signedIn: boolean): RequestHandler {
  return http.get("/v1/auth/sessions", () =>
    signedIn
      ? HttpResponse.json({ items: [], next_cursor: null })
      : HttpResponse.json(
          {
            type: "about:blank",
            title: "Unauthenticated",
            status: 401,
            code: "unauthenticated",
          },
          { status: 401 },
        ),
  );
}

// Default handlers shared by every test: the reads the shell itself makes, and the
// dashboard's (it is the landing page, P0-23), empty. Anything else fails (setup.ts
// listens with the "error" strategy); a test declares the other endpoints it needs, or
// its own answers, with `server.use(...)`.
export const handlers: RequestHandler[] = [
  session(true),
  reviewKinds([]),
  // The project page reads the agent profiles for its header (P1-06): none by default.
  http.get("/v1/agents/profiles", () =>
    HttpResponse.json({ items: [], next_cursor: null }),
  ),
  // The review queue page (P1-13, the shell's review link): nothing to review, and the
  // default working hours its snooze-until-tomorrow reads.
  http.get("*/v1/review", () =>
    HttpResponse.json({
      items: [],
      next_cursor: null,
    } satisfies PageReviewItemOut),
  ),
  http.get("*/v1/settings/working-hours", () =>
    HttpResponse.json(workingHours()),
  ),
  // A project page opened from another page (the review queue's `o`): no such project
  // unless the test declares one.
  http.get("*/v1/projects/:projectId", () =>
    HttpResponse.json(
      {
        type: "about:blank",
        title: "Not found",
        status: 404,
        code: "not_found",
      },
      { status: 404 },
    ),
  ),
  // The Settings rail section's policy editor (P2-05): the FR-5.6 default policy.
  http.get("*/v1/projects/:projectId/policy", ({ params }) =>
    HttpResponse.json({
      project_id: String(params.projectId),
      gated: ["send_email", "merge_main", "deploy_production"],
      allowed: ["push_feature_branch", "open_pull_request", "read"],
      tool_allowlist: [],
      max_concurrent_runs: 2,
      max_run_minutes: 60,
      max_tasks_per_run: 20,
      version: 1,
    } satisfies PolicyOut),
  ),
  // The Context rail's Knowledge section (P1-17): no items, an empty quota. A test with
  // knowledge declares a KnowledgeFake.
  http.get("*/v1/knowledge/documents", () =>
    HttpResponse.json({ items: [], next_cursor: null }),
  ),
  http.get("*/v1/knowledge/quota", ({ request }) =>
    HttpResponse.json({
      project_id: new URL(request.url).searchParams.get("project_id"),
      count: 0,
      used_bytes: 0,
      project_bytes: 0,
      quota_bytes: 10 * 1024 ** 3,
    }),
  ),
  // The shell's app-open ping (P1-18), sent on every signed-in start.
  appOpen,
  ...dashboardDefaults,
  // The focus bar on every page (P2-15): Quiet, nothing to show.
  ...focusDefaults,
  // Unattended runs (P4-04): the window the project's Schedule rail and Settings read;
  // none by default.
  noUnattendedWindow,
];
