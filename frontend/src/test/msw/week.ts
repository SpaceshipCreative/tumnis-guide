// A stateful MSW fake of the project Calendar view's endpoints (P1-12): the week
// (`GET /v1/plan/week/{monday}?project_id=`) and the one write it makes
// (`PATCH /v1/plan/{day}/items/{task_id}`). A PATCH moves the task from `unscheduled` to
// the day's `planned` blocks, so a refetch after it answers what the server would;
// `recorder.sent` holds every request. The shapes follow the generated types.
import { http, HttpResponse, type RequestHandler } from "msw";

import type {
  PlanItemOut,
  TaskRefOut,
  WeekDayOut,
  WeekOut,
} from "../../api/types.gen";
import { Recorder } from "./settings";

export const PROJECT_ID = "0199aa00-0000-7000-8000-000000000001";
export const TASK_45 = "0199aa00-0000-7000-8000-0000000000a1";
export const TASK_30 = "0199aa00-0000-7000-8000-0000000000a2";
export const DUE_TASK = "0199aa00-0000-7000-8000-0000000000a3";
export const PLANNED_TASK = "0199aa00-0000-7000-8000-0000000000a4";
export const PLAN_ID = "0199aa00-0000-7000-8000-0000000000f1";
export const MONDAY = "2026-03-09";

const EDT = (day: string, hhmm: string) => {
  // New York is UTC-4 all week (the US change was Sunday 8 March).
  const [h, m] = hhmm.split(":").map(Number) as [number, number];
  const at = new Date(`${day}T00:00:00Z`);
  at.setUTCHours(h + 4, m);
  return at.toISOString().replace(".000Z", "Z");
};

const workWindow = (day: string) => ({
  start: EDT(day, "09:00"),
  end: EDT(day, "18:00"),
});

const free = (day: string, from: string, to: string) => {
  const start = EDT(day, from);
  const end = EDT(day, to);
  return {
    start,
    end,
    minutes: (Date.parse(end) - Date.parse(start)) / 60_000,
  };
};

const task = (
  id: string,
  title: string,
  overrides: Partial<TaskRefOut> = {},
): TaskRefOut => ({
  id,
  title,
  label: "human",
  status: "backlog",
  estimate_minutes: 30,
  due_on: null,
  ...overrides,
});

const workday = (day: string): WeekDayOut => ({
  day,
  window: workWindow(day),
  free_blocks: [free(day, "09:00", "18:00")],
  events: [],
  due: [],
  planned: [],
});

/**
 * The week of Monday 9 March 2026 in New York for the project "Acme brand refresh":
 * - Monday: the project's "Kickoff with Acme" 11:00 to 12:00; free 09:00 to 11:00 and
 *   12:00 to 18:00.
 * - Tuesday: the project's "Acme standup" 09:00 to 10:00, someone else's meeting (busy,
 *   no title) 12:00 to 13:00; free 10:00 to 12:00 and 13:00 to 18:00, where "Review the
 *   palette" is planned 15:00 to 15:30; "Send logo drafts" is due.
 * - Wednesday to Friday: free all day. Saturday and Sunday: no working hours.
 * - To schedule: "Draft the brand guide" (Human, 45 minutes) and "Pick the type scale"
 *   (Hybrid, 30 minutes).
 */
export function weekPayload(): WeekOut {
  const days = [
    "2026-03-09",
    "2026-03-10",
    "2026-03-11",
    "2026-03-12",
    "2026-03-13",
  ].map(workday);
  const [monday, tuesday] = days as [WeekDayOut, WeekDayOut];
  monday.events = [
    {
      title: "Kickoff with Acme",
      start: EDT(MONDAY, "11:00"),
      end: EDT(MONDAY, "12:00"),
      busy: true,
      matched: true,
    },
  ];
  monday.free_blocks = [
    free(MONDAY, "09:00", "11:00"),
    free(MONDAY, "12:00", "18:00"),
  ];
  const tue = "2026-03-10";
  tuesday.events = [
    {
      title: "Acme standup",
      start: EDT(tue, "09:00"),
      end: EDT(tue, "10:00"),
      busy: true,
      matched: true,
    },
    {
      title: null,
      start: EDT(tue, "12:00"),
      end: EDT(tue, "13:00"),
      busy: true,
      matched: false,
    },
  ];
  tuesday.free_blocks = [
    free(tue, "10:00", "12:00"),
    free(tue, "13:00", "18:00"),
  ];
  tuesday.due = [
    task(DUE_TASK, "Send logo drafts", { due_on: tue, estimate_minutes: 45 }),
  ];
  tuesday.planned = [
    {
      task_id: PLANNED_TASK,
      title: "Review the palette",
      start: EDT(tue, "15:00"),
      end: EDT(tue, "15:30"),
    },
  ];
  const weekend = ["2026-03-14", "2026-03-15"].map((day): WeekDayOut => ({
    day,
    window: null,
    free_blocks: [],
    events: [],
    due: [],
    planned: [],
  }));
  return {
    monday: MONDAY,
    timezone: "America/New_York",
    days: [...days, ...weekend],
    unscheduled: [
      task(TASK_45, "Draft the brand guide", { estimate_minutes: 45 }),
      task(TASK_30, "Pick the type scale", {
        label: "hybrid",
        estimate_minutes: 30,
      }),
    ],
  };
}

interface ScheduleBody {
  block_start: string;
  block_end: string;
  version?: number;
}

export class WeekFake {
  readonly recorder = new Recorder();
  week: WeekOut;
  /** Answer every PATCH with this problem (status 409) instead of scheduling. */
  refuse: { code: string; detail: string } | null = null;

  constructor(week: WeekOut = weekPayload()) {
    this.week = week;
  }

  get handlers(): RequestHandler[] {
    return [
      http.get("*/v1/plan/week/:monday", async ({ request }) => {
        await this.recorder.record(request);
        return HttpResponse.json(this.week);
      }),
      http.patch(
        "*/v1/plan/:day/items/:taskId",
        async ({ request, params }) => {
          const sent = await this.recorder.record(request);
          if (this.refuse) {
            return HttpResponse.json(
              {
                type: "about:blank",
                title: "Conflict",
                status: 409,
                ...this.refuse,
              },
              {
                status: 409,
                headers: { "Content-Type": "application/problem+json" },
              },
            );
          }
          const body = sent.body as ScheduleBody;
          const day = String(params.day);
          const taskId = String(params.taskId);
          const ref = this.week.unscheduled.find((t) => t.id === taskId);
          this.week = {
            ...this.week,
            unscheduled: this.week.unscheduled.filter((t) => t.id !== taskId),
            days: this.week.days.map((d) =>
              d.day === day
                ? {
                    ...d,
                    planned: [
                      ...d.planned,
                      {
                        task_id: taskId,
                        title: ref?.title ?? null,
                        start: body.block_start,
                        end: body.block_end,
                      },
                    ],
                  }
                : d,
            ),
          };
          return HttpResponse.json({
            plan_id: PLAN_ID,
            task_id: taskId,
            day,
            position: 1,
            reason: "Scheduled from the calendar",
            block_start: body.block_start,
            block_end: body.block_end,
            version: 1,
          } satisfies PlanItemOut);
        },
      ),
    ];
  }

  /** The writes sent, as `METHOD /path`. */
  writes(): string[] {
    return this.recorder.writes();
  }
}
