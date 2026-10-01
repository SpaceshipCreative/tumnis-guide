// Settings > Metrics (P1-18, PRD Success metrics): the PRD's success metrics over the last
// two weeks, computed on the server from what Postgres already holds, each next to its
// target. No data yet reads "No data yet" (no target beside it), never 0; the agent
// metrics wait for phase 2 and unattended runs for phase 4. The phase 1 exit gate (working days planned in a row, the
// two-week dogfood clock) shows first. Nothing is sent anywhere but this app's `/v1`.
import { useQuery } from "@tanstack/react-query";

import type { MetricOut } from "../../api/types.gen";
import { Card } from "../common/Card";
import { localDay } from "../dashboard/format";
import { metricsSummaryQuery, workspaceQuery } from "./queries";
import { ERROR, HEADING, HINT, SECTION } from "./styles";

/** The metrics cover today and the 13 days before it: two weeks. */
const RANGE_DAYS = 14;

const LABELS: Record<MetricOut["key"], string> = {
  daily_open_rate: "Daily open rate",
  tasks_completed_per_working_day: "Tasks completed per working day",
  rollover_rate: "Rollover rate",
  estimate_error: "Estimate error",
  agent_share: "Share done by agents",
  agent_acceptance_rate: "Agent result acceptance",
  unattended_runs_per_week: "Unattended overnight runs",
};

/** Counts per day or week; every other metric is a share. */
const COUNTS = new Set<MetricOut["key"]>([
  "tasks_completed_per_working_day",
  "unattended_runs_per_week",
]);

const PERCENT = new Intl.NumberFormat("en-US", {
  style: "percent",
  maximumFractionDigits: 0,
});
const COUNT = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });

/** `day` (`YYYY-MM-DD`) moved by `days`, as `YYYY-MM-DD`. */
function addDays(day: string, days: number): string {
  const at = new Date(`${day}T00:00:00Z`);
  at.setUTCDate(at.getUTCDate() + days);
  return at.toISOString().slice(0, 10);
}

/** The last two weeks of local days, `from` to `to` inclusive. */
export function metricsRange(
  now: Date,
  timeZone: string,
): { from: string; to: string } {
  const to = localDay(now, timeZone);
  return { from: addDays(to, 1 - RANGE_DAYS), to };
}

function shown(metric: MetricOut): string {
  if (metric.available_after !== null)
    return `Available after ${metric.available_after}`;
  if (metric.value === null) return "No data yet";
  return COUNTS.has(metric.key)
    ? COUNT.format(metric.value)
    : PERCENT.format(metric.value);
}

function deviceTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

export function MetricsSection() {
  const workspace = useQuery(workspaceQuery());
  // The range is the workspace's own days; a failed settings read falls back to this device's.
  const timeZone =
    workspace.data?.timezone ??
    (workspace.isError ? deviceTimeZone() : undefined);
  const range =
    timeZone === undefined ? undefined : metricsRange(new Date(), timeZone);
  const summary = useQuery({
    ...metricsSummaryQuery(range ?? { from: "", to: "" }),
    enabled: range !== undefined,
  });
  const data = summary.data;

  return (
    <section aria-labelledby="metrics-title" className={SECTION}>
      <h2 id="metrics-title" className={HEADING}>
        Metrics
      </h2>
      <p className={HINT}>
        How Tumnis is working for you over the last two weeks, measured here on
        your own server. Nothing is sent anywhere.
      </p>
      {data === undefined ? (
        summary.isError ? (
          <p className={ERROR} role="alert">
            The metrics could not be loaded.
          </p>
        ) : (
          <p className={HINT} role="status">
            Loading the metrics…
          </p>
        )
      ) : (
        <>
          <Card title="Planned days in a row">
            <p className="flex items-baseline gap-2">
              <span className="text-3xl font-semibold tabular-nums">
                {data.plan_days_in_a_row}
              </span>
              <span className="text-muted">
                of {data.exit_gate_days} working days
              </span>
            </p>
            <p className={HINT}>
              Working days in a row with a published plan you accepted, swapped
              or removed an item from. Phase 1 is done at {data.exit_gate_days}.
            </p>
          </Card>
          <Card title="Success metrics">
            <dl className="flex flex-col divide-y divide-border">
              {data.metrics.map((metric) => (
                <div
                  key={metric.key}
                  data-metric={metric.key}
                  className="flex flex-col gap-0.5 py-2 first:pt-0 last:pb-0 sm:flex-row sm:items-baseline sm:justify-between sm:gap-4"
                >
                  <dt className="font-medium">{LABELS[metric.key]}</dt>
                  <dd className="flex flex-col sm:items-end">
                    <span
                      className={
                        metric.value === null
                          ? "text-muted"
                          : "font-semibold tabular-nums"
                      }
                    >
                      {shown(metric)}
                    </span>
                    {metric.value !== null && (
                      <span className="text-sm text-muted">
                        Target: {metric.target}
                      </span>
                    )}
                  </dd>
                </div>
              ))}
            </dl>
          </Card>
        </>
      )}
    </section>
  );
}
