// The dashboard (P0-23, FR-1.1, FR-1.2, FR-1.4, FR-1.5, UX 1): "what now?" on one screen.
// Header with today's date in the workspace timezone and the review badge; today's
// calendar strip (P1-10), the Today panel and the activity feed beside the project cards on a laptop (5/12 and 7/12, no page
// scroll at 1280 x 800), one column on a phone with quick add in thumb reach. Each part is
// a card (DS-01, ADR-0012). The route loader has filled the cache, so the first render
// has data. From 16:00 local a Close the day button opens the day-close panel (P1-18, J7)
// through the `?panel=close` search param.
import { useQuery } from "@tanstack/react-query";
import { getRouteApi } from "@tanstack/react-router";
import { useLayoutEffect, useRef } from "react";

import { BUTTON_SECONDARY } from "../common/ui";
import { ActivityFeed } from "./ActivityFeed";
import { CalendarStrip } from "./CalendarStrip";
import { CloseDayPanel } from "./CloseDayPanel";
import { FitOfferList } from "./FitOfferRow";
import { formatToday, localDay, localHour } from "./format";
import { ProjectCardGrid } from "./ProjectCardGrid";
import {
  deployStatusQuery,
  planQuery,
  projectsQuery,
  reviewCountQuery,
  todayQuery,
  workspaceQuery,
} from "./queries";
import { ReviewBadge } from "./ReviewBadge";
import { TodayPanel } from "./TodayPanel";
import type { DashboardProject } from "./types";

const dashboardRoute = getRouteApi("/");

/** The Close the day button shows from this hour, local time (plan default). */
export const CLOSE_DAY_FROM_HOUR = 16;

/** Performance mark once the cards and Today render with data (A0.6, P0-29 reads it). */
export const DASHBOARD_READY_MARK = "tumnis:dashboard-ready";

function deviceTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

function activeInBoardOrder(
  projects: readonly DashboardProject[],
): DashboardProject[] {
  return projects
    .filter(
      (p) => p.archived_at === null && (p.status ?? "active") === "active",
    )
    .sort((a, b) =>
      a.sort_key < b.sort_key ? -1 : a.sort_key > b.sort_key ? 1 : 0,
    );
}

export function DashboardPage() {
  const { panel } = dashboardRoute.useSearch();
  const navigate = dashboardRoute.useNavigate();
  const projects = useQuery(projectsQuery());
  const today = useQuery(todayQuery());
  const reviewCount = useQuery(reviewCountQuery());
  const workspace = useQuery(workspaceQuery());
  const deployStatus = useQuery(deployStatusQuery());
  const timeZone = workspace.data?.timezone ?? deviceTimeZone();
  // The day's plan is read for the workspace's own day, once its zone is known (P1-11).
  const planDay = workspace.data
    ? localDay(new Date(), workspace.data.timezone)
    : undefined;
  const plan = useQuery({
    ...planQuery(planDay ?? ""),
    enabled: planDay !== undefined,
  });

  const allProjects = projects.data?.items ?? [];
  const projectNames = Object.fromEntries(
    allProjects.map((p) => [p.id, p.name]),
  );

  const deploy = Object.fromEntries(
    (deployStatus.data ?? []).map((entry) => [entry.project_id, entry.apps]),
  );

  const now = new Date();
  const closeDay = planDay ?? localDay(now, timeZone);
  const showCloseDay = localHour(now, timeZone) >= CLOSE_DAY_FROM_HOUR;
  const closeDayButton = useRef<HTMLButtonElement>(null);
  const setPanel = (next: "close" | undefined) => {
    void navigate({ search: (prev) => ({ ...prev, panel: next }) });
  };

  const ready = projects.isSuccess && today.isSuccess;
  const marked = useRef(false);
  useLayoutEffect(() => {
    if (!ready || marked.current) return;
    marked.current = true;
    performance.mark(DASHBOARD_READY_MARK);
  }, [ready]);

  return (
    <div className="flex flex-col gap-4 md:h-[calc(100dvh-3rem-var(--tg-header-h))]">
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold">Dashboard</h1>
          <p className="text-sm text-muted">
            {formatToday(new Date(), timeZone)}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {showCloseDay && (
            <button
              ref={closeDayButton}
              type="button"
              className={BUTTON_SECONDARY}
              onClick={() => {
                setPanel("close");
              }}
            >
              Close the day
            </button>
          )}
          <ReviewBadge count={reviewCount.data?.count ?? 0} />
        </div>
      </header>
      <div className="flex flex-col gap-6 md:grid md:min-h-0 md:flex-1 md:grid-cols-12">
        <div className="flex min-h-0 flex-col gap-4 md:col-span-5">
          {planDay !== undefined && <CalendarStrip day={planDay} />}
          <TodayPanel
            items={today.data?.items ?? []}
            total={today.data?.total ?? 0}
            projectNames={projectNames}
            unavailable={today.isError}
            pending={today.isPending}
            {...(planDay === undefined ? {} : { planDay })}
            className="md:flex-1"
          />
          {plan.data && (
            <FitOfferList
              issues={plan.data.issues}
              day={plan.data.day}
              className="shrink-0"
            />
          )}
          <ActivityFeed />
        </div>
        <ProjectCardGrid
          projects={activeInBoardOrder(allProjects)}
          timeZone={timeZone}
          deploy={deploy}
          unavailable={projects.isError}
          pending={projects.isPending}
          className="md:col-span-7"
        />
      </div>
      {panel === "close" && (
        <CloseDayPanel
          day={closeDay}
          onClose={() => {
            setPanel(undefined);
            closeDayButton.current?.focus();
          }}
        />
      )}
    </div>
  );
}
