// The project list (P0-17, FR-2.1): every project in board order with its health, and
// "New project" (name, client, goal and its agent, CreateProjectDialog). Saving opens the
// new project's page. The list is the generated `projectsListProjects`, so a live
// `project` message refreshes it.
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";

import { projectsListProjectsOptions } from "../../api/@tanstack/react-query.gen";
import type { ProjectOut } from "../../api/types.gen";
import { BUTTON, ERROR, TITLE } from "../auth/styles";
import { CreateProjectDialog, PROJECT_LIST } from "./CreateProjectDialog";

const LIST = PROJECT_LIST;

export const HEALTH_LABELS: Record<ProjectOut["health"], string> = {
  on_track: "On track",
  at_risk: "At risk",
  blocked: "Blocked",
};

export function ProjectList() {
  const projects = useQuery(projectsListProjectsOptions(LIST));
  const [adding, setAdding] = useState(false);
  const items = projects.data?.items ?? [];
  return (
    <section className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-2">
        <h1 className={TITLE}>Projects</h1>
        {!adding && (
          <button
            type="button"
            className={BUTTON}
            onClick={() => {
              setAdding(true);
            }}
          >
            New project
          </button>
        )}
      </div>
      {adding && (
        <CreateProjectDialog
          onCancel={() => {
            setAdding(false);
          }}
        />
      )}
      {projects.isError && (
        <p role="alert" className={ERROR}>
          Projects could not be loaded.
        </p>
      )}
      {projects.isSuccess && items.length === 0 && !adding && (
        <p className="text-muted">No projects yet.</p>
      )}
      <ul className="flex flex-col divide-y divide-border rounded-md border border-border bg-surface">
        {items.map((project) => (
          <li
            key={project.id}
            data-project-id={project.id}
            className="flex items-center justify-between gap-3 px-4 py-3"
          >
            <Link
              to="/projects/$projectId"
              params={{ projectId: project.id }}
              className="min-w-0 truncate font-medium"
            >
              {project.name}
            </Link>
            <span className="shrink-0 text-sm text-muted">
              {HEALTH_LABELS[project.health]}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
