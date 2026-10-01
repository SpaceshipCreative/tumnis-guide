// Detour capture at Guardrail (P4-01, FR-10.6): "Switched" to something not in Today asks
// what it is and in which project, then captures it as a task (the server moves it In
// progress and asks whether to go back). The project defaults to the last one picked, so
// a title and Enter is enough; Esc closes the picker. Nothing here is required (FR-10.9).
import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";

import { BUTTON_PRIMARY, BUTTON_QUIET, FIELD, FIELD_LABEL } from "../common/ui";
import { projectsQuery } from "../dashboard/queries";

const LAST_PROJECT = "tumnis:detour-project";

function lastProject(): string | null {
  try {
    return window.localStorage.getItem(LAST_PROJECT);
  } catch {
    return null;
  }
}

function rememberProject(id: string): void {
  try {
    window.localStorage.setItem(LAST_PROJECT, id);
  } catch {
    // A blocked store only loses the default.
  }
}

export function SwitchPicker({
  busy,
  onCapture,
  onCancel,
}: {
  busy: boolean;
  onCapture: (title: string, projectId: string) => void;
  onCancel: () => void;
}) {
  const projects = useQuery(projectsQuery());
  const live = (projects.data?.items ?? []).filter(
    (p) => p.archived_at === null,
  );
  const remembered = lastProject();
  const fallback =
    live.find((p) => p.id === remembered)?.id ?? live[0]?.id ?? "";
  const [title, setTitle] = useState("");
  const [picked, setPicked] = useState<string | null>(null);
  const projectId = picked ?? fallback;
  const titleId = useId();
  const projectFieldId = useId();

  return (
    <form
      aria-label="Switched to"
      className="flex min-w-0 flex-wrap items-end gap-2"
      onKeyDown={(e) => {
        if (e.key === "Escape") onCancel();
      }}
      onSubmit={(e) => {
        e.preventDefault();
        const text = title.trim();
        if (text === "" || projectId === "") return;
        rememberProject(projectId);
        onCapture(text, projectId);
      }}
    >
      <label htmlFor={titleId} className={`${FIELD_LABEL} min-w-0 flex-1`}>
        What are you switching to?
        <input
          id={titleId}
          className={FIELD}
          value={title}
          maxLength={200}
          autoFocus
          onChange={(e) => {
            setTitle(e.target.value);
          }}
        />
      </label>
      <label htmlFor={projectFieldId} className={FIELD_LABEL}>
        Project
        <select
          id={projectFieldId}
          className={FIELD}
          value={projectId}
          onChange={(e) => {
            setPicked(e.target.value);
          }}
        >
          {live.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </label>
      <button
        type="submit"
        className={BUTTON_PRIMARY}
        disabled={busy || title.trim() === "" || projectId === ""}
      >
        Capture
      </button>
      <button type="button" className={BUTTON_QUIET} onClick={onCancel}>
        Cancel
      </button>
    </form>
  );
}
