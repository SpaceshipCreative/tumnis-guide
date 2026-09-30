// Project settings (P0-24, FR-2.7, FR-3.8): the subtask threshold; empty means the
// workspace default.
import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";

import { workspaceQuery } from "../../settings/queries";
import { useUpdateProject } from "../mutations";
import type { Project } from "../types";
import { fieldClass, RailSection, saveClass } from "./RailSection";

const DEFAULT_THRESHOLD = 30; // the workspace default until settings load (FR-3.8)

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

export function SettingsSection({
  project,
  open,
  onToggle,
}: {
  project: Project;
  open: boolean;
  onToggle: () => void;
}) {
  const workspace = useQuery(workspaceQuery());
  const workspaceDefault =
    workspace.data?.subtask_threshold_min ?? DEFAULT_THRESHOLD;
  const summary =
    project.subtask_threshold_min == null
      ? "Subtask threshold: workspace default"
      : `Subtask threshold: ${String(project.subtask_threshold_min)} min`;
  return (
    <RailSection
      title="Settings"
      summary={summary}
      open={open}
      onToggle={onToggle}
    >
      <SettingsForm
        key={project.version}
        project={project}
        workspaceDefault={workspaceDefault}
      />
    </RailSection>
  );
}
