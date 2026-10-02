// MSW handlers for close the day and the local metrics (P1-18): `GET
// /v1/day/{day}/summary`, `GET /v1/metrics/summary` and the app-open ping `POST
// /v1/metrics/open`. Bodies are typed with the generated response types.
import { http, HttpResponse, type RequestHandler } from "msw";

import type {
  DaySummaryOut,
  MetricOut,
  MetricsSummaryOut,
  RolloverRef,
  TaskRef,
} from "../../api/types.gen";

const PROJECT = "0199aa00-0000-7000-8000-000000000001";

export function taskRef(
  n: number,
  title: string,
  label: TaskRef["label"] = "human",
): TaskRef {
  return {
    task_id: `0199aa00-0000-7000-8000-${String(n).padStart(12, "0")}`,
    project_id: PROJECT,
    title,
    label,
  };
}

export function rolloverRef(
  n: number,
  title: string,
  rolloverCount: number,
): RolloverRef {
  return {
    ...taskRef(n, title),
    rollover_count: rolloverCount,
    tonight: rolloverCount + 1,
  };
}

/** A Monday with something in every section but the overnight queue. */
export const FULL_DAY: DaySummaryOut = {
  day: "2026-03-09",
  timezone: "America/New_York",
  shipped: [
    taskRef(1, "Write Acme proposal"),
    taskRef(2, "Summarise the brief", "ai"),
  ],
  agents_finished: [taskRef(2, "Summarise the brief", "ai")],
  prepared_by_agents: 1,
  queued_overnight: [],
  queued_unattended: [],
  rolls_over: [
    rolloverRef(3, "Book the venue", 0),
    rolloverRef(4, "Call the printer", 3),
  ],
};

/** A day where nothing happened. */
export const EMPTY_DAY: DaySummaryOut = {
  day: "2026-03-09",
  timezone: "America/New_York",
  shipped: [],
  agents_finished: [],
  prepared_by_agents: 0,
  queued_overnight: [],
  queued_unattended: [],
  rolls_over: [],
};

/** `GET /v1/day/:day/summary` answering `summary`. */
export function daySummary(summary: DaySummaryOut): RequestHandler {
  return http.get("*/v1/day/:day/summary", () => HttpResponse.json(summary));
}

/** `POST /v1/metrics/open`: the app-start ping (204). */
export const appOpen: RequestHandler = http.post(
  "*/v1/metrics/open",
  () => new HttpResponse(null, { status: 204 }),
);

function metric(
  key: MetricOut["key"],
  value: number | null,
  target: string,
  availableAfter: MetricOut["available_after"] = null,
): MetricOut {
  return { key, value, target, available_after: availableAfter };
}

/** Two working weeks in: three planned days in a row, phase 2 and 4 metrics empty. */
export const METRICS: MetricsSummaryOut = {
  start: "2026-02-24",
  end: "2026-03-09",
  plan_days_in_a_row: 3,
  exit_gate_days: 10,
  metrics: [
    metric("daily_open_rate", 0.857, "5 of 7 days"),
    metric("tasks_completed_per_working_day", 2.5, "+50% vs a 2-week baseline"),
    metric("rollover_rate", 0.1, "under 20% of planned tasks"),
    metric("estimate_error", null, "under 30%"),
    metric("agent_share", null, "30% of completed tasks", "phase 2"),
    metric(
      "agent_acceptance_rate",
      null,
      "70% accepted without rework",
      "phase 2",
    ),
    metric("unattended_runs_per_week", null, "at least 3 per week", "phase 4"),
  ],
};

/** `GET /v1/metrics/summary` answering `summary`; `onRequest` sees each query string. */
export function metricsSummary(
  summary: MetricsSummaryOut,
  onRequest?: (query: URLSearchParams) => void,
): RequestHandler {
  return http.get("*/v1/metrics/summary", ({ request }) => {
    onRequest?.(new URL(request.url).searchParams);
    return HttpResponse.json(summary);
  });
}
