// MSW handlers for the day calendar strip and Settings > Working hours (P1-10). The
// shapes follow the generated types, so an API change fails type-checking here first.
// Tests add them with `server.use(...)` and read what was sent from a `Recorder`.
import { http, HttpResponse, type RequestHandler } from "msw";

import type { DayCalendarOut, WorkingHoursOut } from "../../api/types.gen";
import type { Recorder } from "./settings";

/**
 * Monday 2026-03-09 in New York: the window 09:00 to 18:00 EDT (13:00 to 22:00 UTC), a
 * stand-up and a design review from two accounts, an optional lunch that does not block,
 * and the three free blocks around them.
 */
export const DAY_CALENDAR: DayCalendarOut = {
  timezone: "America/New_York",
  window: { start: "2026-03-09T13:00:00Z", end: "2026-03-09T22:00:00Z" },
  events: [
    {
      title: "Standup",
      start: "2026-03-09T15:00:00Z",
      end: "2026-03-09T15:30:00Z",
      busy: true,
      account: "avery@example.com",
    },
    {
      title: "Optional lunch",
      start: "2026-03-09T16:00:00Z",
      end: "2026-03-09T17:00:00Z",
      busy: false,
      account: "avery@example.com",
    },
    {
      title: "Design review",
      start: "2026-03-09T17:00:00Z",
      end: "2026-03-09T18:00:00Z",
      busy: true,
      account: "blake@example.org",
    },
  ],
  free_blocks: [
    {
      start: "2026-03-09T13:00:00Z",
      end: "2026-03-09T15:00:00Z",
      minutes: 120,
    },
    { start: "2026-03-09T15:30:00Z", end: "2026-03-09T17:00:00Z", minutes: 90 },
    {
      start: "2026-03-09T18:00:00Z",
      end: "2026-03-09T22:00:00Z",
      minutes: 240,
    },
  ],
};

/** `GET /v1/plan/{day}/calendar` answering `body` for any day; `onRequest` sees the day. */
export function dayCalendar(
  body: DayCalendarOut = DAY_CALENDAR,
  onRequest?: (day: string) => void,
): RequestHandler {
  return http.get("*/v1/plan/:day/calendar", ({ params }) => {
    onRequest?.(String(params.day));
    return HttpResponse.json(body);
  });
}

/** An empty day: no window (a weekend), no events. */
export const NO_WINDOW: DayCalendarOut = {
  timezone: "America/New_York",
  window: null,
  events: [],
  free_blocks: [],
};

export function workingHours(
  overrides: Partial<WorkingHoursOut> = {},
): WorkingHoursOut {
  return {
    days: [0, 1, 2, 3, 4].map((weekday) => ({
      weekday,
      start: "09:00",
      end: "18:00",
    })),
    version: 2,
    ...overrides,
  };
}

/** `GET` and `PUT /v1/settings/working-hours`; the PUT answers the days it was sent. */
export function workingHoursHandlers(
  recorder: Recorder,
  loaded: WorkingHoursOut = workingHours(),
): RequestHandler[] {
  return [
    http.get("*/v1/settings/working-hours", () => HttpResponse.json(loaded)),
    http.put("*/v1/settings/working-hours", async ({ request }) => {
      const sent = await recorder.record(request);
      const body = sent.body as Pick<WorkingHoursOut, "days">;
      return HttpResponse.json({
        days: body.days,
        version: loaded.version + 1,
      } satisfies WorkingHoursOut);
    }),
  ];
}
