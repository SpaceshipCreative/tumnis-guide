// Active projects in board order (P0-23, FR-1.1). On a laptop the grid scrolls inside its
// own region past ten projects, so the page itself never does (UX 1).
import { Link } from "@tanstack/react-router";
import { useId } from "react";

import type { AppDeployStatus } from "../project/DeployStatus";
import { ProjectCard } from "./ProjectCard";
import type { DashboardProject } from "./types";

export function ProjectCardGrid({
  projects,
  timeZone,
  deploy = {},
  unavailable = false,
  pending = false,
  className = "",
}: {
  projects: readonly DashboardProject[];
  timeZone: string;
  /** Linked Coolify applications' deploy status by project id (P2-14). */
  deploy?: Readonly<Record<string, readonly AppDeployStatus[]>>;
  /** The project list failed to load. */
  unavailable?: boolean;
  /** No answer yet (a retry after a failure): claim nothing. */
  pending?: boolean;
  className?: string;
}) {
  const headingId = useId();
  return (
    <section
      aria-labelledby={headingId}
      className={`flex min-h-0 flex-col gap-2 ${className}`}
    >
      <h2 id={headingId} className="text-base font-semibold">
        Projects
      </h2>
      {pending ? null : unavailable ? (
        <p className="text-sm text-muted">Projects could not be loaded.</p>
      ) : projects.length === 0 ? (
        <p className="text-sm text-muted">
          No active projects yet.{" "}
          <Link to="/projects" className="font-medium text-accent underline">
            Add one
          </Link>
        </p>
      ) : (
        <ul className="grid min-h-0 grid-cols-1 content-start gap-2 sm:grid-cols-2 md:grid-cols-1 md:overflow-y-auto lg:grid-cols-2 xl:grid-cols-3">
          {projects.map((project) => (
            <li key={project.id}>
              <ProjectCard
                project={project}
                timeZone={timeZone}
                deploy={deploy[project.id]}
              />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
