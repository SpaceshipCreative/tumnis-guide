// One project on the dashboard (P0-23, FR-1.1): name, health, next milestone, open count
// and last agent activity, compact so ten fit at 1280 x 800: the name on its own line,
// then health and open count, then milestone and agent activity.
import { Link } from "@tanstack/react-router";
import { useId } from "react";

import { formatDay, formatInstant } from "./format";
import { HealthBadge } from "./HealthBadge";
import type { DashboardProject } from "./types";

export function ProjectCard({
  project,
  timeZone,
}: {
  project: DashboardProject;
  timeZone: string;
}) {
  const nameId = useId();
  const activity = project.last_agent_activity_at;
  return (
    <article
      aria-labelledby={nameId}
      data-project-id={project.id}
      className="flex h-full flex-col gap-1 rounded-lg border border-border bg-surface px-3 py-2"
    >
      <h3 id={nameId} className="min-w-0 text-sm font-semibold">
        <Link
          to="/projects/$projectId"
          params={{ projectId: project.id }}
          className="block truncate hover:underline"
        >
          {project.name}
        </Link>
      </h3>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
        <HealthBadge health={project.health} />
        <span>
          <span data-testid="open-count">{project.open_count}</span> open
        </span>
      </div>
      <p className="flex flex-wrap gap-x-2 text-xs text-muted">
        <span>
          {project.next_milestone === null
            ? "No milestone"
            : `Next milestone ${formatDay(project.next_milestone)}`}
        </span>
        <span>
          {activity
            ? `Agent active ${formatInstant(activity, timeZone)}`
            : "No agent activity yet"}
        </span>
      </p>
    </article>
  );
}
