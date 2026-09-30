import { http, HttpResponse, type RequestHandler } from "msw";

import type { PageReviewItemOut } from "../../api/types.gen";
import { dashboardDefaults } from "./dashboard";
import { workingHours } from "./planning";

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
  ...dashboardDefaults,
];
