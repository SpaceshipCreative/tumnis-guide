// The project list (P0-17, FR-2.1): every project in board order with its health, and
// "New project" (name, client, goal). Saving opens the new project's page. The list is
// the generated `projectsListProjects`, so a live `project` message refreshes it.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { useState, type SyntheticEvent } from "react";

import {
  projectsGetProjectQueryKey,
  projectsListProjectsOptions,
  projectsListProjectsQueryKey,
} from "../../api/@tanstack/react-query.gen";
import type { ProjectOut } from "../../api/types.gen";
import { apiWrite, useWrite } from "../../lib/fetch";
import { BUTTON, ERROR, FORM, INPUT, LABEL, TITLE } from "../auth/styles";

const LIST = { query: { limit: 200 } };

export const HEALTH_LABELS: Record<ProjectOut["health"], string> = {
  on_track: "On track",
  at_risk: "At risk",
  blocked: "Blocked",
};

interface NewProject {
  name: string;
  client: string | null;
  goal: string | null;
  idempotencyKey?: string;
}

function NewProjectForm({ onCancel }: { onCancel: () => void }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [client, setClient] = useState("");
  const [goal, setGoal] = useState("");
  const [error, setError] = useState<string | null>(null);
  const create = useWrite<NewProject, ProjectOut>({
    mutationFn: ({ idempotencyKey, ...body }) =>
      apiWrite<ProjectOut>({
        kind: "create",
        method: "POST",
        path: "/projects",
        body: { ...body },
        idempotencyKey,
      }),
    onSuccess: (project) => {
      queryClient.setQueryData(
        projectsGetProjectQueryKey({ path: { project_id: project.id } }),
        project,
      );
      void queryClient.invalidateQueries({
        queryKey: projectsListProjectsQueryKey(LIST),
      });
      void navigate({
        to: "/projects/$projectId",
        params: { projectId: project.id },
      });
    },
    onError: (failure) => {
      setError(failure.message);
    },
  });

  function submit(event: SyntheticEvent) {
    event.preventDefault();
    setError(null);
    create.mutate({
      name: name.trim(),
      client: client.trim() || null,
      goal: goal.trim() || null,
    });
  }

  return (
    <form aria-label="New project" className={FORM} onSubmit={submit}>
      <label className={LABEL}>
        Name
        <input
          className={INPUT}
          value={name}
          onChange={(e) => {
            setName(e.target.value);
          }}
          required
          maxLength={120}
          autoFocus
        />
      </label>
      <label className={LABEL}>
        Client
        <input
          className={INPUT}
          value={client}
          onChange={(e) => {
            setClient(e.target.value);
          }}
        />
      </label>
      <label className={LABEL}>
        Goal
        <input
          className={INPUT}
          value={goal}
          onChange={(e) => {
            setGoal(e.target.value);
          }}
          maxLength={280}
        />
      </label>
      {error && (
        <p role="alert" className={ERROR}>
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <button type="submit" className={BUTTON} disabled={create.isPending}>
          Save
        </button>
        <button
          type="button"
          className="rounded-md px-4 py-2 text-muted"
          onClick={onCancel}
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

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
        <NewProjectForm
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
