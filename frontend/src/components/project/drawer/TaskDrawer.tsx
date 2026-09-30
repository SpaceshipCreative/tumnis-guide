// The task drawer (P0-24, FR-3.5, UX 9): `?task=<id>` opens it; the title, the status
// action, the repeat rule, comments, and Move to trash (undoable). A side panel on a
// laptop, a full sheet on the phone; Escape or Close shuts it.
import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";

import { taskQueryOptions, useUpdateTask } from "../../../lib/optimistic";
import { formatDay, formatMinutes } from "../../dashboard/format";
import { STATUS_WORDS, useChangeStatus, useTrashTask } from "../mutations";
import { fieldClass, saveClass } from "../rail/RailSection";
import { statusAction } from "../TaskRow";
import type { Task } from "../types";
import { CommentList } from "./CommentList";
import { RecurrencePicker } from "./RecurrencePicker";

function TitleForm({ task }: { task: Task }) {
  const [title, setTitle] = useState(task.title);
  const update = useUpdateTask();
  const id = useId();
  return (
    <form
      className="flex flex-col gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        const next = title.trim();
        if (next === "" || next === task.title) return;
        update.mutate({
          id: task.id,
          patch: { title: next },
          version: task.version,
        });
      }}
    >
      <label htmlFor={id} className="text-sm font-medium">
        Title
      </label>
      <input
        id={id}
        type="text"
        value={title}
        maxLength={500}
        onChange={(e) => {
          setTitle(e.target.value);
        }}
        className={fieldClass}
      />
      <button type="submit" disabled={update.isPending} className={saveClass}>
        Save
      </button>
    </form>
  );
}

function TaskDetails({ task, onClose }: { task: Task; onClose: () => void }) {
  const change = useChangeStatus();
  const trash = useTrashTask();
  const action = statusAction(task);
  return (
    <div className="flex flex-col gap-5">
      <TitleForm key={task.version} task={task} />
      <dl className="grid grid-cols-2 gap-2 text-sm">
        <dt className="text-muted">Status</dt>
        <dd>{STATUS_WORDS[task.status]}</dd>
        <dt className="text-muted">Estimate</dt>
        <dd>
          {task.estimate_minutes === null
            ? "No estimate"
            : formatMinutes(task.estimate_minutes)}
        </dd>
        <dt className="text-muted">Due</dt>
        <dd>{task.due_on ? formatDay(task.due_on) : "No due date"}</dd>
      </dl>
      {task.first_action && (
        <p className="text-sm">First action: {task.first_action}</p>
      )}
      <div className="flex flex-wrap gap-2">
        {action && (
          <button
            type="button"
            disabled={change.isPending}
            onClick={() => {
              change.mutate({ task, to: action.to });
            }}
            className={saveClass}
          >
            {action.text}
          </button>
        )}
        <button
          type="button"
          disabled={trash.isPending}
          onClick={() => {
            trash.mutate({ task });
            onClose();
          }}
          className={`${saveClass} text-danger`}
        >
          Move to trash
        </button>
      </div>
      <RecurrencePicker task={task} />
      <CommentList taskId={task.id} />
    </div>
  );
}

export function TaskDrawer({
  taskId,
  laptop,
  onClose,
}: {
  taskId: string;
  laptop: boolean;
  onClose: () => void;
}) {
  const task = useQuery(taskQueryOptions(taskId));
  const panel = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    panel.current?.focus();
    return () => {
      if (opener?.isConnected) opener.focus();
    };
  }, [taskId]);
  const data = task.data as Task | undefined;
  return (
    <div
      className={
        laptop
          ? "fixed inset-y-0 right-0 z-40 flex w-[28rem] max-w-full"
          : "fixed inset-0 z-40 flex"
      }
    >
      <div
        ref={panel}
        role="dialog"
        aria-modal={laptop ? undefined : "true"}
        aria-labelledby={titleId}
        tabIndex={-1}
        onKeyDown={(event) => {
          if (event.key === "Escape") onClose();
        }}
        className="flex w-full flex-col gap-4 overflow-y-auto border-l border-border bg-surface px-4 py-4 shadow-xl outline-none md:px-6"
      >
        <div className="flex items-start justify-between gap-2">
          <h2
            id={titleId}
            className="min-w-0 text-lg font-semibold break-words"
          >
            {data?.title ?? (task.isError ? "Task not found" : "Loading…")}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="min-h-11 shrink-0 rounded-md px-3 text-sm font-medium text-accent md:min-h-8"
          >
            Close
          </button>
        </div>
        {data ? (
          <TaskDetails task={data} onClose={onClose} />
        ) : task.isError ? (
          <p className="text-sm text-muted">
            This task is not here any more; it may have moved to the trash.
          </p>
        ) : null}
      </div>
    </div>
  );
}
