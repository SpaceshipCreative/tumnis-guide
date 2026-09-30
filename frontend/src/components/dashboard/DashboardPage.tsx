// The dashboard (P0-23, FR-1.1, FR-1.2, FR-1.4, FR-1.5, UX 1): "what now?" on one screen.
// Header with today's date in the workspace timezone and the review badge; today's
// calendar strip (P1-10), the Today panel and the activity feed beside the project cards on a laptop (5/12 and 7/12, no page
// scroll at 1280 x 800), one column on a phone with quick add in thumb reach. Each part is
// a card (DS-01, ADR-0012). The route loader has filled the cache, so the first render
// has data.
import { useQuery } from "@tanstack/react-query";
import { useLayoutEffect, useRef } from "react";

import { ActivityFeed } from "./ActivityFeed";
import { CalendarStrip } from "./CalendarStrip";
import { formatToday, localDay } from "./format";
import { ProjectCardGrid } from "./ProjectCardGrid";
import {
  deployStatusQuery,
  projectsQuery,
  reviewCountQuery,
  todayQuery,
  workspaceQuery,
} from "./queries";
import { ReviewBadge } from "./ReviewBadge";
import { TodayPanel } from "./TodayPanel";
import type { DashboardProject } from "./types";

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
  const projects = useQuery(projectsQuery());
  const today = useQuery(todayQuery());
  const reviewCount = useQuery(reviewCountQuery());
  const workspace = useQuery(workspaceQuery());
  const deployStatus = useQuery(deployStatusQuery());
  const timeZone = workspace.data?.timezone ?? deviceTimeZone();

  const allProjects = projects.data?.items ?? [];
  const projectNames = Object.fromEntries(
    allProjects.map((p) => [p.id, p.name]),
  );

  const deploy = Object.fromEntries(
    (deployStatus.data ?? []).map((entry) => [entry.project_id, entry.apps]),
  );

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
        <ReviewBadge count={reviewCount.data?.count ?? 0} />
      </header>
      <div className="flex flex-col gap-6 md:grid md:min-h-0 md:flex-1 md:grid-cols-12">
        <div className="flex min-h-0 flex-col gap-4 md:col-span-5">
          {workspace.data && (
            <CalendarStrip
              day={localDay(new Date(), workspace.data.timezone)}
            />
          )}
          <TodayPanel
            items={today.data?.items ?? []}
            total={today.data?.total ?? 0}
            projectNames={projectNames}
            unavailable={today.isError}
            pending={today.isPending}
            className="md:flex-1"
          />
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
    </div>
  );
}
