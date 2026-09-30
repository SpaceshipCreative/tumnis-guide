// One task in the Tasks view (P0-24, FR-2.6, UX 7): its title opens the drawer; one status
// action (Start, or Done for a Human task in progress); subtasks indented underneath. A
// capture still on the offline queue shows with its pending mark and no actions (P0-25).
import { BUTTON_SECONDARY } from "../common/ui";
// The label chip (P1-07): pending, suggested or confirmed, overridden with one click.
import { LabelChip, useLabelOverride } from "../common/LabelChip";
import { formatDay, formatMinutes } from "../dashboard/format";
import { PendingMark } from "../quickadd/PendingMark";
import { isPendingId, pendingKey } from "../quickadd/queue";
import type { TaskLite } from "./grouping";
import { useChangeStatus, type Status } from "./mutations";
import type { Task } from "./types";

/** A row's task: list rows carry the label's source, reason and suggestion too. */
type RowTask = TaskLite &
  Partial<Pick<Task, "label_source" | "label_reason" | "label_suggestion">>;

function RowLabel({ task }: { task: RowTask }) {
  const { picked, pick } = useLabelOverride(task);
  return (
    <LabelChip
      label={picked ?? task.label}
      source={picked ? "user" : (task.label_source ?? null)}
      reason={picked ? null : (task.label_reason ?? null)}
      suggestion={picked ? null : (task.label_suggestion ?? null)}
      onOverride={pick}
      showReason={false}
    />
  );
}

/** The row's one status action (P0-18's human edges). */
export function statusAction(
  task: TaskLite,
): { to: Status; text: "Start" | "Done"; key: "s" | "d" } | null {
  if (task.status === "today" || task.status === "backlog") {
    return { to: "in_progress", text: "Start", key: "s" };
  }
  if (task.status === "in_progress" && task.label === "human") {
    return { to: "done", text: "Done", key: "d" };
  }
  return null;
}

export function TaskRow({
  task,
  subtasksOf,
  onOpen,
}: {
  task: RowTask;
  /** The task's subtasks shown with it (nested ones included, recursively). */
  subtasksOf: (taskId: string) => readonly TaskLite[];
  onOpen: (taskId: string) => void;
}) {
  const subtasks = subtasksOf(task.id);
  const change = useChangeStatus();
  const pending = isPendingId(task.id);
  const action = pending ? null : statusAction(task);
  const estimate =
    task.label === "ai" || task.estimate_minutes === null
      ? null
      : formatMinutes(task.estimate_minutes);
  return (
    <li data-task-title={task.title} data-task-id={task.id} className="py-2">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0 flex-1">
          <button
            type="button"
            data-task-open
            disabled={pending}
            title="Open (Enter)"
            onClick={() => {
              onOpen(task.id);
            }}
            className="max-w-full truncate text-left font-medium hover:underline disabled:no-underline"
          >
            {task.title}
          </button>
          <p className="flex flex-wrap items-center gap-x-2 text-xs text-muted">
            {pending && <PendingMark idempotencyKey={pendingKey(task.id)} />}
            {pending ? <span>No label yet</span> : <RowLabel task={task} />}
            {estimate && <span>{estimate}</span>}
            {task.due_on && <span>Due {formatDay(task.due_on)}</span>}
          </p>
        </div>
        {action && (
          <button
            type="button"
            data-status-action={action.key}
            aria-label={`${action.text} ${task.title}`}
            title={`${action.text} (${action.key})`}
            disabled={change.isPending}
            onClick={() => {
              change.mutate({ task, to: action.to });
            }}
            className={`${BUTTON_SECONDARY} shrink-0 md:min-h-8 md:py-1`}
          >
            {action.text}
          </button>
        )}
      </div>
      {subtasks.length > 0 && (
        <ul className="mt-2 ml-4 flex flex-col gap-1 border-l border-border pl-3">
          {subtasks.map((sub) => (
            <TaskRow
              key={sub.id}
              task={sub}
              subtasksOf={subtasksOf}
              onOpen={onOpen}
            />
          ))}
        </ul>
      )}
    </li>
  );
}
