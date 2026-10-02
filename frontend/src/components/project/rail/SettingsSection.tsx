// The project rail's Settings section. P0-24 (FR-2.7, FR-3.8): the subtask threshold;
// empty means the workspace default. P1-02 (Data flow rule 6): the local decisions only
// switch. Turning it on keeps every decision about the project's items on the local vLLM
// model; nothing is sent to Jev. A toggle sends one PATCH /v1/projects/{id} with the
// version it read; a 409 shows the current value with a notice (REL-2). P2-05 (FR-5.6):
// the approval policy editor. P2-15 (FR-10.1): the focus check-in cadence. P2-18 (FR-2.1,
// APP-13): Archive and Unarchive (ArchiveProject.tsx). P3-09 (FR-5.10): Purge project,
// for an archived project only.
// In the rail, RailSections opens one section at a time (`open`, `onToggle`); rendered on
// its own, the section starts open and toggles itself.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useId, useState } from "react";
import type * as z from "zod";

import type { ProjectOut } from "../../../api/types.gen";
import { zProjectOut } from "../../../api/zod.gen";
import {
  ApiError,
  apiWrite,
  ConflictError,
  useWrite,
} from "../../../lib/fetch";
import { BUTTON_DANGER } from "../../common/ui";
import { PurgeDialog } from "../../settings/connections/PurgeDialog";
import { workspaceQuery } from "../../settings/queries";
import { ArchiveControl } from "../ArchiveProject";
import { FocusCadence } from "../FocusCadence";
import { useUpdateProject } from "../mutations";
import { PolicyEditor } from "../PolicyEditor";
import { projectQuery } from "../queries";
import type { Project } from "../types";
import { fieldClass, RailSection, saveClass } from "./RailSection";

const DEFAULT_THRESHOLD = 30; // the workspace default until settings load (FR-3.8)

type Saved = z.infer<typeof zProjectOut>;

function SettingsForm({
  project,
  workspaceDefault,
}: {
  project: Project;
  workspaceDefault: number;
}) {
  const [threshold, setThreshold] = useState(
    project.subtask_threshold_min == null
      ? ""
      : String(project.subtask_threshold_min),
  );
  const update = useUpdateProject();
  const id = useId();
  return (
    <form
      className="flex flex-col gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        const value = threshold.trim() === "" ? null : Number(threshold);
        update.mutate({ project, patch: { subtask_threshold_min: value } });
      }}
    >
      <label htmlFor={id} className="text-sm">
        Subtask threshold (minutes)
      </label>
      <input
        id={id}
        type="number"
        inputMode="numeric"
        min={1}
        max={960}
        placeholder={`${String(workspaceDefault)} (workspace default)`}
        value={threshold}
        onChange={(e) => {
          setThreshold(e.target.value);
        }}
        className={fieldClass}
      />
      <p className="text-xs text-muted">
        Subtasks at or above this many minutes get their own card.
      </p>
      <button type="submit" disabled={update.isPending} className={saveClass}>
        Save settings
      </button>
    </form>
  );
}

function LocalDecisionsSwitch({ project }: { project: Project }) {
  const queryClient = useQueryClient();
  const ids = { label: useId(), hint: useId() };
  const [written, setSaved] = useState({
    localOnly: project.local_decisions_only ?? false,
    version: project.version,
  });
  // A refetch that brings a newer project (a rename elsewhere, a live update, a saved
  // threshold) wins over what this switch last wrote, so the next toggle sends the
  // current version.
  const saved =
    project.version > written.version
      ? {
          localOnly: project.local_decisions_only ?? false,
          version: project.version,
        }
      : written;
  const [message, setMessage] = useState<string | null>(null);

  function show(current: Saved) {
    queryClient.setQueryData(
      projectQuery(project.id).queryKey,
      current as ProjectOut,
    );
    setSaved({
      localOnly: current.local_decisions_only,
      version: current.version,
    });
  }

  const save = useWrite<
    { local_decisions_only: boolean; version: number; idempotencyKey?: string },
    Saved
  >({
    mutationFn: ({ version, idempotencyKey, ...body }) =>
      apiWrite({
        kind: "update",
        method: "PATCH",
        path: `/projects/${project.id}`,
        body,
        version,
        idempotencyKey,
        schema: zProjectOut,
      }),
    onSuccess: show,
    onError: (error) => {
      if (error instanceof ConflictError) {
        const current = zProjectOut.safeParse(error.current);
        if (current.success) {
          show(current.data);
          setMessage(
            "This project changed elsewhere; you are now seeing the current setting.",
          );
          return;
        }
      }
      setMessage(
        error instanceof ApiError
          ? (error.problem.detail ?? error.message)
          : "The setting could not be saved.",
      );
    },
  });

  function toggle() {
    setMessage(null);
    save.mutate({
      local_decisions_only: !saved.localOnly,
      version: saved.version,
    });
  }

  return (
    <div className="flex min-w-0 flex-col gap-2 border-t border-border pt-3">
      <div className="flex min-w-0 items-center justify-between gap-3">
        <span id={ids.label} className="text-sm font-medium">
          Local decisions only
        </span>
        <button
          type="button"
          role="switch"
          aria-checked={saved.localOnly}
          aria-labelledby={ids.label}
          aria-describedby={ids.hint}
          disabled={save.isPending}
          onClick={toggle}
          className={`relative inline-flex min-h-11 min-w-16 shrink-0 items-center rounded-full border border-border px-1 disabled:opacity-60 ${
            saved.localOnly ? "bg-accent" : "bg-surface"
          }`}
        >
          <span
            aria-hidden="true"
            className={`h-7 w-7 rounded-full bg-text transition-transform ${
              saved.localOnly ? "translate-x-7" : "translate-x-0"
            }`}
          />
        </button>
      </div>
      <p id={ids.hint} className="text-sm text-muted">
        Decisions about this project&apos;s items run on the local model and are
        never sent to Jev. They are judged more strictly, so more of them come
        to you for review.
      </p>
      {message ? (
        <p role="status" className="text-sm text-muted">
          {message}
        </p>
      ) : null}
    </div>
  );
}

// P3-09 (FR-5.10): an archived project can be purged for good, with a reason (audited);
// the purge removes the email, chat and notes only it holds. The project then is gone,
// so the page goes back to the project list.
function ProjectPurge({ project }: { project: Project }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [purging, setPurging] = useState(false);
  if (!project.archived_at) return null;
  return (
    <div className="flex min-w-0 flex-col gap-2 border-t border-border pt-3">
      <p className="text-sm text-muted">
        Purging deletes this archived project and the ingested content only it
        holds. It cannot be undone.
      </p>
      <button
        type="button"
        className={BUTTON_DANGER}
        onClick={() => {
          setPurging(true);
        }}
      >
        Purge project
      </button>
      {purging && (
        <PurgeDialog
          scope="project"
          targetId={project.id}
          name={project.name}
          onCancel={() => {
            setPurging(false);
          }}
          onDone={() => {
            setPurging(false);
            void queryClient.invalidateQueries({
              queryKey: projectQuery(project.id).queryKey,
            });
            void navigate({ to: "/projects" });
          }}
        />
      )}
    </div>
  );
}

export function SettingsSection({
  project,
  open,
  onToggle,
}: {
  project: Project;
  open?: boolean;
  onToggle?: () => void;
}) {
  const [ownOpen, setOwnOpen] = useState(true);
  const workspace = useQuery(workspaceQuery());
  const workspaceDefault =
    workspace.data?.subtask_threshold_min ?? DEFAULT_THRESHOLD;
  const threshold =
    project.subtask_threshold_min == null
      ? "Subtask threshold: workspace default"
      : `Subtask threshold: ${String(project.subtask_threshold_min)} min`;
  const summary = project.local_decisions_only
    ? `${threshold} · Local decisions only`
    : threshold;
  return (
    <RailSection
      title="Settings"
      summary={summary}
      open={open ?? ownOpen}
      onToggle={
        onToggle ??
        (() => {
          setOwnOpen((current) => !current);
        })
      }
    >
      <SettingsForm
        key={project.version}
        project={project}
        workspaceDefault={workspaceDefault}
      />
      <FocusCadence
        key={`focus-${String(project.version)}`}
        project={project}
      />
      <LocalDecisionsSwitch project={project} />
      <PolicyEditor project={project} />
      <ArchiveControl project={project} />
      <ProjectPurge project={project} />
    </RailSection>
  );
}
