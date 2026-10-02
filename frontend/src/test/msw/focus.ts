// MSW handlers for the focus bar (P2-15): `GET /v1/focus/current` and the replies
// (`POST /v1/focus/respond`, `POST /v1/focus/less`). Bodies follow the generated types.
import { http, HttpResponse, type RequestHandler } from "msw";

import type {
  FocusCurrentOut,
  FocusMessageOut,
  FocusSessionOut,
} from "../../api/types.gen";

/** Nothing to show: Quiet, no session, no messages (the default for every test). */
export function quietFocus(): FocusCurrentOut {
  return {
    level: "quiet",
    workspace_level: "quiet",
    override_level: null,
    session: null,
    messages: [],
    guardrail: null,
    detour: null,
    next_step: null,
  };
}

/** A session on `title` started `minutesAgo` minutes before now. */
export function focusSession(
  title: string,
  minutesAgo: number,
  overrides: Partial<FocusSessionOut> = {},
): FocusSessionOut {
  return {
    id: crypto.randomUUID(),
    task_id: crypto.randomUUID(),
    title,
    started_at: new Date(Date.now() - minutesAgo * 60_000).toISOString(),
    cadence_min: 25,
    doubled: false,
    next_check_in_at: null,
    ...overrides,
  };
}

/** A focus message fired now. */
export function focusMessage(
  overrides: Partial<FocusMessageOut> = {},
): FocusMessageOut {
  return {
    id: crypto.randomUUID(),
    kind: "check_in_due",
    task_id: null,
    level: "coach",
    rule: "Coach · check_in_due (25 min cadence)",
    message: "Still on it?",
    fired_at: new Date().toISOString(),
    response: null,
    speak: false,
    clip_id: null,
    ...overrides,
  };
}

/** `GET /v1/focus/current` answering `body`. */
export function focusCurrent(body: FocusCurrentOut): RequestHandler {
  return http.get("*/v1/focus/current", () => HttpResponse.json(body));
}

/** The focus replies, recorded: each POST body in `calls`, answered with `answer`. */
export function focusReplies(answer: FocusCurrentOut) {
  const calls: { path: string; body: unknown }[] = [];
  const record =
    (path: string) =>
    async ({ request }: { request: Request }) => {
      calls.push({ path, body: await request.json() });
      return HttpResponse.json(answer);
    };
  const handlers: RequestHandler[] = [
    http.post("*/v1/focus/respond", record("/focus/respond")),
    http.post("*/v1/focus/less", record("/focus/less")),
    http.put("*/v1/focus/level", record("/focus/level")),
  ];
  return { calls, handlers };
}

export const focusDefaults: RequestHandler[] = [focusCurrent(quietFocus())];
