// MSW handlers for Settings > Calendar (P1-09): two Google accounts, one of them waiting
// for a new consent after Google revoked its grant. Tests add them with `server.use(...)`
// and read what was sent from a `Recorder`.
import { http, HttpResponse, type RequestHandler } from "msw";

import type { CalendarAccountOut } from "../../api/types.gen";

import type { Recorder } from "./settings";

export type CalendarAccount = CalendarAccountOut;

export const CONSENT_URL =
  "https://accounts.google.com/o/oauth2/v2/auth?client_id=client-123&state=abc";

export const CALENDAR_ACCOUNTS: readonly CalendarAccount[] = [
  {
    id: "01890000-0000-7000-8000-0000000000c1",
    connection_id: "01890000-0000-7000-8000-0000000000d1",
    google_email: "avery@example.com",
    status: "connected",
    calendars: [
      {
        id: "avery@example.com",
        summary: "Avery",
        primary: true,
        time_zone: "America/New_York",
      },
      {
        id: "team@group.example.com",
        summary: "Team calendar",
        primary: false,
        time_zone: "America/New_York",
      },
    ],
    selected_calendar_ids: ["avery@example.com"],
    last_sync_at: "2026-03-09T12:00:00Z",
    version: 3,
  },
  {
    id: "01890000-0000-7000-8000-0000000000c2",
    connection_id: "01890000-0000-7000-8000-0000000000d2",
    google_email: "blake@example.org",
    status: "needs_reauth",
    calendars: [
      {
        id: "blake@example.org",
        summary: "Blake",
        primary: true,
        time_zone: "America/New_York",
      },
    ],
    selected_calendar_ids: ["blake@example.org"],
    last_sync_at: null,
    version: 1,
  },
];

export function calendarHandlers(recorder: Recorder): RequestHandler[] {
  return [
    http.get("*/v1/calendar/accounts", () =>
      HttpResponse.json(CALENDAR_ACCOUNTS),
    ),
    http.get("*/v1/calendar/oauth/start", () =>
      HttpResponse.json({ url: CONSENT_URL }),
    ),
    http.get("*/v1/settings/calendar.google", () =>
      HttpResponse.json({
        section: "calendar.google",
        values: { client_id: "client-123" },
        secrets_set: ["client_secret"],
        version: 1,
      }),
    ),
    http.put(
      "*/v1/calendar/accounts/:accountId/calendars",
      async ({ request, params }) => {
        const sent = await recorder.record(request);
        const account = CALENDAR_ACCOUNTS.find(
          (a) => a.id === params.accountId,
        );
        if (!account) return new HttpResponse(null, { status: 404 });
        const body = sent.body as { selected_calendar_ids: string[] };
        return HttpResponse.json({
          ...account,
          selected_calendar_ids: body.selected_calendar_ids,
          version: account.version + 1,
        });
      },
    ),
    http.post(
      "*/v1/calendar/accounts/:accountId/sync",
      async ({ request, params }) => {
        await recorder.record(request);
        const account = CALENDAR_ACCOUNTS.find(
          (a) => a.id === params.accountId,
        );
        if (!account) return new HttpResponse(null, { status: 404 });
        return HttpResponse.json(account, { status: 202 });
      },
    ),
  ];
}
