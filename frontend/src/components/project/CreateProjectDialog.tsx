// The new-project form (P0-17, P1-06, FR-2.1): name, client, goal and the project's agent,
// a new Hermes profile from the project template (the default) or an existing profile
// linked by name. Saving opens the new project's page, where the header follows the
// agent's provisioning. The body names `profile` only for a link; without it the server
// creates a new profile.
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useId, useState, type SyntheticEvent } from "react";

import {
  projectsGetProjectQueryKey,
  projectsListProjectsQueryKey,
} from "../../api/@tanstack/react-query.gen";
import type { AgentProfileChoice, ProjectOut } from "../../api/types.gen";
import { apiWrite, useWrite } from "../../lib/fetch";
import { BUTTON, ERROR, FORM, INPUT, LABEL } from "../auth/styles";

export const PROJECT_LIST = { query: { limit: 200 } };
// A Hermes profile name, as the server checks it (agents' NAME_RE).
const PROFILE_NAME = "[a-z0-9][a-z0-9\\-]{0,62}";

interface NewProject {
  name: string;
  client: string | null;
  goal: string | null;
  profile?: AgentProfileChoice;
  idempotencyKey?: string;
}

type AgentMode = "create" | "link";

export function CreateProjectDialog({ onCancel }: { onCancel: () => void }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [client, setClient] = useState("");
  const [goal, setGoal] = useState("");
  const [mode, setMode] = useState<AgentMode>("create");
  const [profileName, setProfileName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const hintId = useId();
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
        queryKey: projectsListProjectsQueryKey(PROJECT_LIST),
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
      ...(mode === "link"
        ? { profile: { mode: "link", name: profileName.trim() } }
        : {}),
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
      <fieldset className="flex flex-col gap-2">
        <legend className="text-sm font-medium">Agent</legend>
        <label className="flex min-h-11 items-center gap-2 text-sm">
          <input
            type="radio"
            name="agent-mode"
            value="create"
            checked={mode === "create"}
            onChange={() => {
              setMode("create");
            }}
          />
          Create a new agent profile
        </label>
        <label className="flex min-h-11 items-center gap-2 text-sm">
          <input
            type="radio"
            name="agent-mode"
            value="link"
            checked={mode === "link"}
            onChange={() => {
              setMode("link");
            }}
          />
          Link an existing profile
        </label>
        {mode === "link" && (
          <div className="flex flex-col gap-1">
            <label className={LABEL}>
              Profile name
              <input
                className={INPUT}
                value={profileName}
                onChange={(e) => {
                  setProfileName(e.target.value);
                }}
                required
                pattern={PROFILE_NAME}
                maxLength={63}
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                aria-describedby={hintId}
              />
            </label>
            <p id={hintId} className="text-sm text-muted">
              The Hermes profile on the agent server: lower-case letters, digits
              and dashes.
            </p>
          </div>
        )}
      </fieldset>
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
