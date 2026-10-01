// The project's focus check-in cadence (P2-15, FR-10.1, FR-10.4): minutes between
// check-ins on this project's In progress tasks at Coach and above; empty means the
// default (25). One PATCH /v1/projects/{id} with the version read, as the other rail
// settings save (a 409 shows the current value).
import { useId, useState } from "react";

import { useUpdateProject } from "./mutations";
import { fieldClass, saveClass } from "./rail/RailSection";
import type { Project } from "./types";

export const DEFAULT_CADENCE_MIN = 25; // FR-10.1

export function FocusCadence({ project }: { project: Project }) {
  const [cadence, setCadence] = useState(
    project.focus_cadence_min == null ? "" : String(project.focus_cadence_min),
  );
  const update = useUpdateProject();
  const id = useId();
  const hint = useId();
  return (
    <form
      className="flex min-w-0 flex-col gap-2 border-t border-border pt-3"
      onSubmit={(event) => {
        event.preventDefault();
        const value = cadence.trim() === "" ? null : Number(cadence);
        update.mutate({ project, patch: { focus_cadence_min: value } });
      }}
    >
      <label htmlFor={id} className="text-sm">
        Focus check-in cadence (minutes)
      </label>
      <input
        id={id}
        type="number"
        inputMode="numeric"
        min={5}
        max={240}
        aria-describedby={hint}
        placeholder={`${String(DEFAULT_CADENCE_MIN)} (default)`}
        value={cadence}
        onChange={(e) => {
          setCadence(e.target.value);
        }}
        className={fieldClass}
      />
      <p id={hint} className="text-xs text-muted">
        At Coach, Tumnis checks in this often while one of this project&apos;s
        tasks is In progress. Two &ldquo;still on it&rdquo; answers in a row
        double it for the rest of the task.
      </p>
      <button type="submit" disabled={update.isPending} className={saveClass}>
        Save cadence
      </button>
    </form>
  );
}
