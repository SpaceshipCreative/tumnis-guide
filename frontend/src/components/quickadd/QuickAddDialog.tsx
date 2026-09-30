// Quick add (P0-25, FR-3.3, J2): a title and a project, Enter. The project is required;
// on a project page it starts as that project. The capture goes on the offline queue
// (online it is sent at once), a notice says where it went, and the dialog closes.
// Label: none yet, so the task starts pending (R-08); Jev labels it from P1-07.
import { useSelector } from "@xstate/react";
import { useEffect, useId, useRef, useState } from "react";

import { persistent } from "../../lib/idb";
import { uiStore } from "../../stores/uiStore";
import { type ProjectChoice, ProjectTypeahead } from "./ProjectTypeahead";
import { useEnqueueTask, useQueueActor } from "./queue";

export const QUICKADD_READY_MARK = "tumnis:quickadd-ready";

export function QuickAddDialog({
  defaultProject,
  onClose,
}: {
  defaultProject: ProjectChoice | null;
  onClose: () => void;
}) {
  const headingId = useId();
  const errorId = useId();
  const [title, setTitle] = useState("");
  const [project, setProject] = useState<ProjectChoice | null>(defaultProject);
  const [error, setError] = useState<string | null>(null);
  const titleRef = useRef<HTMLInputElement>(null);
  const projectRef = useRef<HTMLInputElement>(null);
  const enqueue = useEnqueueTask();
  const online = useSelector(useQueueActor(), (s) => s.context.online);

  useEffect(() => {
    const opener =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    titleRef.current?.focus();
    performance.mark(QUICKADD_READY_MARK);
    return () => {
      opener?.focus();
    };
  }, []);

  const submit = () => {
    const trimmed = title.trim();
    if (trimmed === "") {
      setError("Add a title");
      titleRef.current?.focus();
      return;
    }
    if (!project) {
      setError("Pick a project");
      projectRef.current?.focus();
      return;
    }
    enqueue({ project_id: project.id, title: trimmed });
    uiStore.trigger.showNotice({
      text: online
        ? `Added to ${project.name}`
        : `Added to ${project.name}. It syncs when you are back online.`,
    });
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 px-4 pt-[15vh]"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={headingId}
        className="w-full max-w-lg rounded-lg border border-border bg-surface p-4 shadow-xl"
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            onClose();
          }
        }}
      >
        <h2 id={headingId} className="mb-3 text-base font-semibold">
          Quick add
        </h2>
        <form
          noValidate
          className="flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <label className="flex flex-col gap-1 text-sm font-medium">
            Title
            <input
              ref={titleRef}
              type="text"
              value={title}
              maxLength={500}
              aria-invalid={error === "Add a title" || undefined}
              aria-describedby={error ? errorId : undefined}
              onChange={(event) => {
                setTitle(event.target.value);
                if (error === "Add a title") setError(null);
              }}
              className="min-h-11 w-full rounded-md border border-border bg-bg px-3 text-base font-normal md:min-h-9 md:text-sm"
            />
          </label>
          <ProjectTypeahead
            value={project}
            inputRef={projectRef}
            invalid={error === "Pick a project"}
            describedBy={error ? errorId : undefined}
            onChange={(choice) => {
              setProject(choice);
              if (choice && error === "Pick a project") setError(null);
            }}
          />
          {error && (
            <p id={errorId} role="alert" className="text-sm text-danger">
              {error}
            </p>
          )}
          {!persistent() && (
            <p className="text-xs text-muted">
              Offline capture is off in this browser mode.
            </p>
          )}
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="min-h-11 rounded-md px-3 text-sm font-medium hover:bg-surface-muted md:min-h-9"
            >
              Cancel
            </button>
            <button
              type="submit"
              className="min-h-11 rounded-md bg-accent px-4 text-sm font-medium text-accent-contrast md:min-h-9"
            >
              Add
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
