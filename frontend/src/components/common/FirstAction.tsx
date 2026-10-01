// The first-action line (P1-08, UX 5, FR-4.6): the Generation slot's placeholder shows
// first with its marker, a task still waiting shows `First action pending`, and the
// project agent's value (or the person's own) replaces both once `/ws` refetches the task.
import { useQuery } from "@tanstack/react-query";

import { taskQueryOptions } from "../../lib/optimistic";

export type FirstActionState = "placeholder" | "pending" | "agent" | "user";

interface FirstActionFields {
  first_action: string | null;
  first_action_source?: string | null;
}

export function firstActionState(task: FirstActionFields): FirstActionState {
  if (!task.first_action?.trim()) return "pending";
  if (task.first_action_source === "placeholder") return "placeholder";
  return task.first_action_source === "agent" ? "agent" : "user";
}

/** The line for a task already in hand (the drawer, a card). */
export function FirstActionLine({ task }: { task: FirstActionFields }) {
  const state = firstActionState(task);
  return (
    <p data-testid="first-action" data-state={state} className="text-sm">
      <span className="text-muted">First action: </span>
      {state === "pending" ? (
        <span className="text-muted italic">First action pending</span>
      ) : (
        <span>{task.first_action}</span>
      )}
      {state === "placeholder" && (
        <span className="ml-2 rounded border border-border px-1 text-xs text-muted">
          suggested while the project agent works
        </span>
      )}
    </p>
  );
}

/** The line for one task, read (and refreshed over `/ws`) by id. */
export function TaskFirstAction({ taskId }: { taskId: string }) {
  const task = useQuery(taskQueryOptions(taskId));
  if (!task.data) return null;
  return <FirstActionLine task={task.data} />;
}
