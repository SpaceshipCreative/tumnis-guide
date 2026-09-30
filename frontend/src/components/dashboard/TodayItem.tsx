// One Today task (P0-23, FR-1.2, UX 3, UX 5): title, project, label, estimate (Human and
// Hybrid only, FR-3.1), the first action, and one primary action: Start a Today task, or
// Done for a Human task in progress (the only in_progress -> done edge, P0-18).
import { useQueryClient } from "@tanstack/react-query";

import { apiWrite, ConflictError, useWrite } from "../../lib/fetch";
import { uiStore } from "../../stores/uiStore";
import { BUTTON_SECONDARY } from "../common/ui";
import { formatMinutes } from "./format";
import { invalidateTaskReads } from "./queries";
import type { TaskLabel, TaskStatus, TodayTask } from "./types";

const LABEL_TEXT: Record<TaskLabel, string> = {
  human: "Human",
  ai: "AI",
  hybrid: "Hybrid",
};

interface StatusChange {
  task: TodayTask;
  to: TaskStatus;
  idempotencyKey?: string;
}

function primaryAction(
  task: TodayTask,
): { to: TaskStatus; text: string } | null {
  if (task.status === "today") return { to: "in_progress", text: "Start" };
  if (task.status === "in_progress" && task.label === "human") {
    return { to: "done", text: "Done" };
  }
  return null;
}

function PrimaryAction({ task }: { task: TodayTask }) {
  const queryClient = useQueryClient();
  const change = useWrite<StatusChange, unknown>({
    mutationFn: ({ task: t, to, idempotencyKey }) =>
      apiWrite({
        kind: "update",
        method: "POST",
        path: `/tasks/${t.id}/status`,
        body: { to },
        version: t.version,
        idempotencyKey,
      }),
    onError: (error) => {
      if (error instanceof ConflictError) {
        uiStore.trigger.showConflict({ entity: "task" });
      }
    },
    onSettled: () => invalidateTaskReads(queryClient),
  });
  const action = primaryAction(task);
  if (action === null) return null;
  return (
    <button
      type="button"
      aria-label={`${action.text} ${task.title}`}
      disabled={change.isPending}
      onClick={() => {
        change.mutate({ task, to: action.to });
      }}
      className={`${BUTTON_SECONDARY} shrink-0 md:min-h-8`}
    >
      {action.text}
    </button>
  );
}

export function TodayItem({
  task,
  projectName,
}: {
  task: TodayTask;
  projectName: string;
}) {
  // Human and Hybrid only: an AI task's estimate is never shown (FR-3.1, FR-4.4).
  const estimate =
    task.label === "human" || task.label === "hybrid"
      ? task.estimate_minutes
      : null;
  return (
    <li className="flex flex-col gap-1 py-2 first:pt-0 last:pb-0">
      <div className="flex items-center justify-between gap-2">
        <span className="min-w-0 truncate font-medium">{task.title}</span>
        <PrimaryAction task={task} />
      </div>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
        <span className="rounded bg-surface-muted px-1.5 py-0.5 text-text">
          {projectName}
        </span>
        <span className="rounded border border-border px-1.5 py-0.5">
          {task.label === null ? "No label yet" : LABEL_TEXT[task.label]}
        </span>
        {estimate !== null && <span>{formatMinutes(estimate)}</span>}
      </div>
      {task.first_action === null ? (
        <p className="text-sm text-muted italic">No first action yet</p>
      ) : (
        <p className="truncate text-sm">First action: {task.first_action}</p>
      )}
    </li>
  );
}
