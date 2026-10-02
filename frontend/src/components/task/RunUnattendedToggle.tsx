// Run unattended (P4-04, FR-4.5, SAF-1): an AI task's drawer offers a "Run unattended"
// switch that queues it for tonight's window (`PUT /v1/tasks/{id}/unattended`), on when
// the task reads as queued (`unattended_queued_at`). A tainted task never runs unattended,
// so it shows "Needs you: from outside content" instead of the switch; a Human, Hybrid or
// unlabelled task shows nothing. A save refreshes the task and the day close's list.
import { useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import type { UnattendedOut } from "../../api/types.gen";
import { ApiError, apiWrite, useWrite } from "../../lib/fetch";
import type { TaskOut } from "../../lib/optimistic";
import { invalidateTaskViews, queryId } from "../../lib/task-cache";

const NEEDS_YOU = "Needs you: from outside content";

export function RunUnattendedToggle({ task }: { task: TaskOut }) {
  if (task.label !== "ai") return null;
  if (task.tainted) {
    return <p className="text-sm text-muted">{NEEDS_YOU}</p>;
  }
  return <UnattendedSwitch task={task} />;
}

function UnattendedSwitch({ task }: { task: TaskOut }) {
  const queryClient = useQueryClient();
  const ids = { label: useId(), hint: useId() };
  const [failure, setFailure] = useState<string | null>(null);
  const save = useWrite<
    { taskId: string; queued: boolean; idempotencyKey?: string },
    UnattendedOut
  >({
    mutationFn: ({ taskId, queued, idempotencyKey }) =>
      apiWrite<UnattendedOut>({
        kind: "create",
        method: "PUT",
        path: `/tasks/${encodeURIComponent(taskId)}/unattended`,
        body: { queued },
        idempotencyKey,
      }),
    onSuccess: () => {
      setFailure(null);
    },
    onError: (error) => {
      setFailure(
        error instanceof ApiError
          ? (error.problem.detail ?? error.message)
          : "That did not save; try again.",
      );
    },
    onSettled: () =>
      Promise.all([
        invalidateTaskViews(queryClient),
        queryClient.invalidateQueries({
          predicate: (query) =>
            queryId(query.queryKey) === "planningGetDaySummary",
        }),
      ]),
  });
  // While the save is on its way the switch shows what was asked for.
  const asked = save.isPending ? save.variables?.queued : undefined;
  const queued = asked ?? task.unattended_queued_at !== null;
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <div className="flex min-w-0 items-center justify-between gap-3">
        <span id={ids.label} className="text-sm font-medium">
          Run unattended
        </span>
        <button
          type="button"
          role="switch"
          aria-checked={queued}
          aria-labelledby={ids.label}
          aria-describedby={ids.hint}
          disabled={save.isPending}
          onClick={() => {
            setFailure(null);
            save.mutate({ taskId: task.id, queued: !queued });
          }}
          className={`relative inline-flex min-h-11 min-w-16 shrink-0 items-center rounded-full border border-border px-1 disabled:opacity-60 ${
            queued ? "bg-accent" : "bg-surface"
          }`}
        >
          <span
            aria-hidden="true"
            className={`h-7 w-7 rounded-full bg-text transition-transform ${
              queued ? "translate-x-7" : "translate-x-0"
            }`}
          />
        </button>
      </div>
      <p id={ids.hint} className="text-sm text-muted">
        Runs tonight in the unattended window; the result waits for you in the
        morning.
      </p>
      {failure !== null && (
        <p role="alert" className="text-sm text-danger">
          {failure}
        </p>
      )}
    </div>
  );
}
