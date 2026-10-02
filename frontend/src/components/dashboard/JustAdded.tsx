// Just added (A1.1, FR-3.3, FR-4.1, coordinator decision 83): what the person quick-added
// today and has not yet planned, started or finished, newest first and at most three, so a
// capture made on the dashboard shows there, after a reload too, with its label chip (the
// one-line reason beside it, overridden with one click), its first action and its
// estimate. A capture still on the offline queue shows at once with its pending mark. The
// section is hidden when there is nothing, keeping the dashboard on one screen (A0.1).
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import type { TaskOut } from "../../api/types.gen";
import { Card } from "../common/Card";
import { firstActionState } from "../common/FirstAction";
import { TaskLabel } from "../common/LabelChip";
import { PendingMark } from "../quickadd/PendingMark";
import { pendingKey, pendingRow, usePendingTasks } from "../quickadd/queue";
import { formatMinutes } from "./format";
import { justAddedQuery } from "./queries";

/** At most this many rows, the server's own cap. */
export const JUST_ADDED_LIMIT = 3;

/** The first action: the element holds the value alone (the placeholder's marker sits
 * beside it), so a reader gets exactly what to do first. */
function FirstAction({ task }: { task: TaskOut }) {
  const state = firstActionState(task);
  return (
    <p className="flex min-w-0 flex-wrap items-baseline gap-x-1 text-sm">
      <span className="text-muted">First action:</span>
      <span
        data-testid="first-action"
        data-state={state}
        className={state === "pending" ? "text-muted italic" : "min-w-0"}
      >
        {state === "pending" ? "First action pending" : task.first_action}
      </span>
      {state === "placeholder" && (
        <span className="rounded border border-border px-1 text-xs text-muted">
          suggested while the project agent works
        </span>
      )}
    </p>
  );
}

function TaskItem({
  task,
  onOpen,
}: {
  task: TaskOut;
  onOpen: (taskId: string) => void;
}) {
  const estimate =
    task.label === "ai" || task.estimate_minutes === null
      ? null
      : formatMinutes(task.estimate_minutes);
  return (
    <li className="flex flex-col gap-1 py-2 first:pt-0 last:pb-0">
      <button
        type="button"
        onClick={() => {
          onOpen(task.id);
        }}
        className="max-w-full truncate text-left font-medium hover:underline"
      >
        {task.title}
      </button>
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
        <TaskLabel task={task} />
        {estimate !== null && (
          <span data-testid="estimate-chip">{estimate}</span>
        )}
      </div>
      <FirstAction task={task} />
    </li>
  );
}

function PendingItem({ id, title }: { id: string; title: string }) {
  return (
    <li className="flex flex-col gap-1 py-2 first:pt-0 last:pb-0">
      <span className="truncate font-medium">{title}</span>
      <p className="flex flex-wrap items-center gap-x-2 text-xs text-muted">
        <PendingMark idempotencyKey={pendingKey(id)} />
        <span>No label yet</span>
      </p>
    </li>
  );
}

export function JustAdded({
  onOpen,
  className = "",
}: {
  onOpen: (taskId: string) => void;
  className?: string;
}) {
  const query = useQuery(justAddedQuery());
  const queued = usePendingTasks();
  const items = query.data?.items;
  // A synced capture can be on the list a moment before its queue entry goes: the
  // server's row stands for it.
  const pending = useMemo(
    () =>
      queued
        .map(pendingRow)
        .filter(
          (row) =>
            !(items ?? []).some(
              (t) => t.project_id === row.project_id && t.title === row.title,
            ),
        )
        .reverse(),
    [queued, items],
  );
  const shown = (items ?? []).slice(
    0,
    Math.max(0, JUST_ADDED_LIMIT - pending.length),
  );
  if (pending.length === 0 && shown.length === 0) return null;
  return (
    <Card title="Just added" className={className}>
      <ul className="flex flex-col divide-y divide-border">
        {pending.slice(0, JUST_ADDED_LIMIT).map((row) => (
          <PendingItem key={row.id} id={row.id} title={row.title} />
        ))}
        {shown.map((task) => (
          <TaskItem key={task.id} task={task} onOpen={onOpen} />
        ))}
      </ul>
    </Card>
  );
}
