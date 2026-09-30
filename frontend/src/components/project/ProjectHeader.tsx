// The project header (P0-24, FR-2.4): name, goal, health, the next milestone, today's
// tasks with their estimates and the sum, and the agent's status.
import { formatDay, formatMinutes } from "../dashboard/format";
import { HealthBadge } from "../dashboard/HealthBadge";
import type { TaskLite } from "./grouping";
import type { Project } from "./types";

export function ProjectHeader({
  project,
  today,
}: {
  project: Project;
  today: readonly TaskLite[];
}) {
  const total = today.reduce((sum, t) => sum + (t.estimate_minutes ?? 0), 0);
  return (
    <header className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="min-w-0 text-2xl font-semibold break-words">
          {project.name}
        </h1>
        <HealthBadge health={project.health} />
      </div>
      {project.goal && <p className="text-muted">{project.goal}</p>}
      <dl className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted">
        <div>
          <dt className="sr-only">Milestone</dt>
          <dd>
            {project.next_milestone
              ? `Next milestone: ${formatDay(project.next_milestone)}`
              : "No milestone set"}
          </dd>
        </div>
        <div>
          <dt className="sr-only">Agent</dt>
          <dd>No agent yet</dd>
        </div>
      </dl>
      {today.length === 0 ? (
        <p className="text-sm text-muted">Nothing planned for today</p>
      ) : (
        <div className="flex flex-col gap-1">
          <ul
            aria-label="Today's tasks"
            className="flex flex-col gap-1 text-sm"
          >
            {today.map((task) => (
              <li
                key={task.id}
                className="flex items-baseline justify-between gap-3"
              >
                <span className="min-w-0 truncate">{task.title}</span>
                <span className="shrink-0 text-muted">
                  {task.estimate_minutes === null
                    ? "No estimate"
                    : formatMinutes(task.estimate_minutes)}
                </span>
              </li>
            ))}
          </ul>
          <p className="text-sm font-medium">Today: {formatMinutes(total)}</p>
        </div>
      )}
    </header>
  );
}
