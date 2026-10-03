// MSW handlers for the day calendar strip and Settings > Working hours (P1-10). The
// shapes follow the generated types, so an API change fails type-checking here first.
// Tests add them with `server.use(...)` and read what was sent from a `Recorder`.
import { http, HttpResponse, type RequestHandler } from "msw";

import type {
  AlternateOut,
  DayCalendarOut,
  PlanIssueOut,
  PlanItemViewOut,
  PlanOut,
  WorkingHoursOut,
} from "../../api/types.gen";
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

// --- The day's plan (P1-11) -------------------------------------------------------------

const ACME = "0199aa00-0000-7000-8000-0000000000a1";
const DOGFOOD = "0199aa00-0000-7000-8000-0000000000a2";

/** A plan item as `GET /v1/plan/{day}` shows it; `n` makes its ids and position. */
export function planItem(
  n: number,
  overrides: Partial<PlanItemViewOut> = {},
): PlanItemViewOut {
  const hex = n.toString(16).padStart(12, "0");
  return {
    id: `0199aa00-0000-7000-8001-${hex}`,
    task_id: `0199aa00-0000-7000-8002-${hex}`,
    position: n,
    reason: `Reason ${String(n)}`,
    block: null,
    accepted_at: null,
    removed_at: null,
    swapped_from_task_id: null,
    title: `Task ${String(n)}`,
    project_id: ACME,
    project_name: "Acme site",
    label: "human",
    estimate_minutes: 60,
    first_action: `First step ${String(n)}`,
    status: "backlog",
    blocked: false,
    version: 1,
    ...overrides,
  };
}

/**
 * Monday 2026-03-09 in New York (EDT, UTC-4), the master's plan: a Human hour at 09:00,
 * a Hybrid half hour at 10:00, an AI task that runs anytime, and a Human hour at 13:00.
 */
export function mondayPlan(overrides: Partial<PlanOut> = {}): PlanOut {
  return {
    id: "0199aa00-0000-7000-8003-000000000001",
    day: "2026-03-09",
    timezone: "America/New_York",
    status: "published",
    source: "master",
    trigger: "morning",
    notice: null,
    fallback_reason: null,
    built_at: "2026-03-09T12:30:00Z",
    items: [
      planItem(1, {
        title: "Send logo drafts",
        reason: "Due today",
        block: { start: "2026-03-09T13:00:00Z", end: "2026-03-09T14:00:00Z" },
      }),
      planItem(2, {
        title: "Book the venue",
        label: "hybrid",
        estimate_minutes: 30,
        project_id: DOGFOOD,
        project_name: "Tumnis dogfood",
        reason: "Client is waiting",
        block: { start: "2026-03-09T14:00:00Z", end: "2026-03-09T14:30:00Z" },
      }),
      planItem(3, {
        title: "Draft the outline",
        label: "ai",
        estimate_minutes: null,
        reason: "Runs while you work",
      }),
      planItem(4, {
        title: "Write the proposal",
        reason: "Rolled over twice",
        block: { start: "2026-03-09T17:00:00Z", end: "2026-03-09T18:00:00Z" },
      }),
    ],
    issues: [],
    ...overrides,
  };
}

/** A plan issue: a 90-minute task with no gap today, offered 60 + 30 or Tuesday. */
export function ninetyMinuteIssue(
  overrides: Partial<PlanIssueOut> = {},
): PlanIssueOut {
  return {
    id: "0199aa00-0000-7000-8004-000000000001",
    task_id: "0199aa00-0000-7000-8002-000000000090",
    title: "Write Acme proposal",
    kind: "no_gap",
    estimate_minutes: 90,
    offer: { split: [60, 30], move_to: "2026-03-10" },
    review_item_id: "0199aa00-0000-7000-8005-000000000001",
    resolved_at: null,
    ...overrides,
  };
}

/** `GET /v1/plan/{day}`: 200 with `null`, no plan published for the day yet (APP-F05,
 * decision 98). */
export function noPlan(): RequestHandler {
  return http.get("*/v1/plan/:day", () => HttpResponse.json(null));
}

/** The alternates a swap offers (`GET /v1/plan/{day}/alternates`). */
export const ALTERNATES: AlternateOut[] = [
  {
    id: "0199aa00-0000-7000-8002-0000000000b1",
    project_id: ACME,
    title: "Call the printer",
    label: "human",
    estimate_minutes: 30,
    due_on: null,
  },
  {
    id: "0199aa00-0000-7000-8002-0000000000b2",
    project_id: DOGFOOD,
    title: "Tag the photos",
    label: "ai",
    estimate_minutes: null,
    due_on: "2026-03-12",
  },
];

const AT = "2026-03-09T12:35:00Z";

// The plan after one write, as the server would answer it.
function applied(
  plan: PlanOut,
  action: string,
  target: string,
  body: unknown,
  alternates: readonly AlternateOut[],
): PlanOut {
  const items = plan.items.map((item) => {
    if (item.task_id !== target || item.removed_at !== null) return item;
    if (action === "accept") return { ...item, accepted_at: AT };
    if (action === "remove") return { ...item, removed_at: AT };
    return item;
  });
  if (action === "accept-all") {
    return {
      ...plan,
      items: items.map((i) => ({ ...i, accepted_at: i.accepted_at ?? AT })),
    };
  }
  if (action === "swap") {
    const withId = (body as { with_task_id?: string } | null)?.with_task_id;
    const alternate = alternates.find((a) => a.id === withId);
    const out = plan.items.find(
      (i) => i.task_id === target && i.removed_at === null,
    );
    if (alternate && out) {
      return {
        ...plan,
        items: [
          ...plan.items.map((i) => (i === out ? { ...i, removed_at: AT } : i)),
          {
            ...out,
            id: `${out.id.slice(0, -4)}ffff`,
            task_id: alternate.id,
            title: alternate.title,
            project_id: alternate.project_id,
            label: alternate.label,
            estimate_minutes: alternate.estimate_minutes,
            block: alternate.label === "ai" ? null : out.block,
            swapped_from_task_id: out.task_id,
          },
        ],
      };
    }
  }
  const issues = plan.issues.map((issue) =>
    issue.id === target && (action === "split" || action === "move")
      ? { ...issue, resolved_at: AT }
      : issue,
  );
  return { ...plan, items, issues };
}

/**
 * The plan's reads and writes, recorded and applied: `GET /v1/plan/{day}` answers the
 * current plan, the alternates answer `alternates`, and each write answers the plan with
 * its change made, or `status` (409 by default, nothing changed) when `fail` names the
 * write (accept, remove, swap, split, move, accept-all). Writes wait for `gate` first,
 * so a test can see the page before the answer.
 */
export function planHandlers(
  recorder: Recorder,
  plan: PlanOut = mondayPlan(),
  {
    alternates = ALTERNATES,
    fail,
    status = 409,
    gate,
  }: {
    alternates?: AlternateOut[];
    fail?: string;
    status?: number;
    gate?: Promise<void>;
  } = {},
): RequestHandler[] {
  let current = plan;
  const write = async ({ request }: { request: Request }) => {
    const sent = await recorder.record(request);
    await gate;
    const parts = sent.path.split("/");
    const action = parts.at(-1) ?? "";
    if (fail === action) {
      return HttpResponse.json(
        { type: "about:blank", title: "Conflict", status, code: "conflict" },
        { status },
      );
    }
    current = applied(
      current,
      action,
      parts.at(-2) ?? "",
      sent.body,
      alternates,
    );
    return HttpResponse.json(current);
  };
  return [
    http.get("*/v1/plan/:day/alternates", () => HttpResponse.json(alternates)),
    http.post("*/v1/plan/replan", async ({ request }) => {
      await recorder.record(request);
      return HttpResponse.json(
        { day: plan.day, workflow_id: "build_plan:replan" },
        { status: 202 },
      );
    }),
    http.get("*/v1/plan/:day", () => HttpResponse.json(current)),
    http.post("*/v1/plan/:day/items/:taskId/:action", write),
    http.post("*/v1/plan/:day/accept-all", write),
    http.post("*/v1/plan/:day/issues/:issueId/:action", write),
  ];
}
