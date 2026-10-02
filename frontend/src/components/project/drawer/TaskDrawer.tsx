// The task drawer (P0-24, FR-3.5, UX 9): `?task=<id>` opens it; the title, the status
// action, the repeat rule, comments, and Move to trash (undoable). A side panel on a
// laptop, a full sheet on the phone; Escape or Close shuts it. P2-04: an AI or Hybrid
// task has Run; `?run=<id>` shows that run (RunView) in the drawer, Back returns. P4-04:
// an AI task has the Run unattended switch (read from the task, no extra request).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, useEffect, useId, useRef, useState } from "react";

import type { RunRequested } from "../../../api/types.gen";
import { ApiError, apiWrite, useWrite } from "../../../lib/fetch";

import {
  taskQueryKey,
  taskQueryOptions,
  useUpdateTask,
} from "../../../lib/optimistic";
import { invalidateTaskViews } from "../../../lib/task-cache";
import { undo } from "../../../lib/undo";
import { FirstActionLine } from "../../common/FirstAction";
import { TaintBadge } from "../../common/TaintBadge";
import { formatDay, formatMinutes } from "../../dashboard/format";
import { STATUS_WORDS, useChangeStatus, useTrashTask } from "../mutations";
import { deleteClass, fieldClass, saveClass } from "../rail/RailSection";
import { statusAction } from "../TaskRow";
import type { Task } from "../types";
import { PacketPreview } from "../PacketPreview";
import { RunUnattendedToggle } from "../../task/RunUnattendedToggle";
import { CommentList } from "./CommentList";
import { PullRequests } from "./PullRequests";
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

// The acceptance criteria in their own order: each run of `- ` lines as a list, any
// other line (a Hybrid task's `AI part:` and `Your part:`, P1-08) as text where it stands.
type CriteriaBlock =
  { kind: "list"; items: string[] } | { kind: "text"; text: string };

function criteriaBlocks(text: string | null): CriteriaBlock[] {
  const blocks: CriteriaBlock[] = [];
  for (const line of (text ?? "").split("\n")) {
    if (line.trim() === "") continue;
    const last = blocks.at(-1);
    if (!line.startsWith("- ")) blocks.push({ kind: "text", text: line });
    else if (last?.kind === "list") last.items.push(line.slice(2));
    else blocks.push({ kind: "list", items: [line.slice(2)] });
  }
  return blocks;
}

function Criteria({ text }: { text: string | null }) {
  const blocks = criteriaBlocks(text);
  if (blocks.length === 0) return null;
  return (
    <div className="flex flex-col gap-1 text-sm">
      <h3 className="text-muted">Acceptance criteria</h3>
      {blocks.map((block, index) =>
        block.kind === "list" ? (
          <ul
            key={index}
            aria-label="Acceptance criteria"
            className="list-disc pl-5 marker:text-muted"
          >
            {block.items.map((item, at) => (
              <li key={at}>{item}</li>
            ))}
          </ul>
        ) : (
          <p key={index}>{block.text}</p>
        ),
      )}
    </div>
  );
}

// `Enriched by agent · Undo` (P1-08, UX 9): shown while the task's latest write is the
// project agent's enrichment; Undo puts back what it replaced (R-09). A read names the
// enrichment's change only at the version it wrote, but a later write's own answer (a
// title edit) carries that write's change id until the read comes back: the change first
// seen is kept, and a different one hides the line (the read then names none, which
// forgets it, so a later enrichment shows again).
function Enriched({ task }: { task: Task }) {
  const queryClient = useQueryClient();
  const [failed, setFailed] = useState(false);
  const [seen, setSeen] = useState<string | null>(null);
  const undoing = useMutation({
    mutationFn: (changeId: string) =>
      undo({
        changeId,
        label: "Enrichment",
        taskId: task.id,
        afterVersion: task.version,
        at: Date.now(),
      }),
    onSuccess: (restored) => {
      queryClient.setQueryData(taskQueryKey(task.id), restored);
      void invalidateTaskViews(queryClient);
    },
    onError: () => {
      setFailed(true);
    },
  });
  // Whatever it filled (criteria or an estimate alone too), a finished enrichment's
  // change is the one the read names.
  const eligible = task.enrichment_status === "done" ? task.change_id : null;
  if (eligible === null && seen !== null) setSeen(null);
  if (eligible !== null && seen === null) setSeen(eligible);
  if (eligible === null || (seen !== null && seen !== eligible)) return null;
  const changeId = eligible;
  return (
    <p className="flex items-center gap-2 text-sm text-muted">
      <span>Enriched by agent</span>
      <span aria-hidden="true">·</span>
      <button
        type="button"
        disabled={undoing.isPending}
        onClick={() => {
          setFailed(false);
          undoing.mutate(changeId);
        }}
        className="min-h-11 font-medium text-accent md:min-h-8"
      >
        Undo
      </button>
      {failed && <span role="status">Could not undo; try again.</span>}
    </p>
  );
}

/** What an enrichment run would be given (FR-15.4), read only when asked for. */
function PacketToggle({ taskId }: { taskId: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="flex flex-col gap-2">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => {
          setOpen((v) => !v);
        }}
        className={saveClass}
      >
        {open ? "Hide packet" : "Preview packet"}
      </button>
      {open && <PacketPreview taskId={taskId} />}
    </div>
  );
}

// The run view loads only when a run is open (`?run=`), so the project page does not
// carry its code (P0-29's blocking-time budget).
const RunView = lazy(() =>
  import("../../runs/RunView").then((m) => ({ default: m.RunView })),
);

// Run (P2-04, FR-5.4): an AI or Hybrid task the agent may still work on asks for a run
// (`POST /v1/tasks/{id}/run`, 202) and the drawer switches to it; the server refuses
// what it cannot run (another run active, no ready agent) and says why. The answer
// names the task it was asked for, so the drawer can drop one that lands after the
// user has moved to another task (or closed the drawer).
const RUNNABLE_LABELS = new Set(["ai", "hybrid"]);
const RUNNABLE_STATUSES = new Set(["backlog", "today", "in_progress"]);

function RunButton({
  task,
  onRun,
}: {
  task: Task;
  onRun: (runId: string, taskId: string) => void;
}) {
  const [refused, setRefused] = useState<string | null>(null);
  const run = useWrite<
    { taskId: string; idempotencyKey?: string },
    RunRequested
  >({
    mutationFn: ({ taskId, idempotencyKey }) =>
      apiWrite<RunRequested>({
        kind: "create",
        method: "POST",
        path: `/tasks/${encodeURIComponent(taskId)}/run`,
        body: { kind: "task" },
        idempotencyKey,
      }),
    onSuccess: (answer, { taskId }) => {
      setRefused(null);
      onRun(answer.run_id, taskId);
    },
    onError: (error) => {
      setRefused(
        error instanceof ApiError
          ? (error.problem.detail ?? error.message)
          : "The run could not be started.",
      );
    },
  });
  if (
    !RUNNABLE_LABELS.has(task.label ?? "") ||
    !RUNNABLE_STATUSES.has(task.status)
  ) {
    return null;
  }
  return (
    <>
      <button
        type="button"
        disabled={run.isPending}
        onClick={() => {
          run.mutate({ taskId: task.id });
        }}
        className={saveClass}
      >
        Run
      </button>
      {refused !== null && (
        <p role="alert" className="w-full text-sm text-danger">
          {refused}
        </p>
      )}
    </>
  );
}

function TaskDetails({
  task,
  onClose,
  onRun,
}: {
  task: Task;
  onClose: () => void;
  onRun: (runId: string, taskId: string) => void;
}) {
  const change = useChangeStatus();
  const trash = useTrashTask();
  const action = statusAction(task);
  return (
    <div className="flex flex-col gap-5">
      {task.tainted && (
        <div>
          <TaintBadge />
        </div>
      )}
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
      <FirstActionLine task={task} />
      <Criteria text={task.acceptance_criteria} />
      <Enriched task={task} />
      <div className="flex flex-wrap gap-2">
        <RunButton key={task.id} task={task} onRun={onRun} />
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
          className={deleteClass}
        >
          Move to trash
        </button>
      </div>
      <RunUnattendedToggle key={task.id} task={task} />
      <RecurrencePicker task={task} />
      <PullRequests taskId={task.id} />
      <PacketToggle key={task.id} taskId={task.id} />
      <CommentList taskId={task.id} />
    </div>
  );
}

export function TaskDrawer({
  taskId,
  runId,
  laptop,
  onClose,
  onRun = () => undefined,
}: {
  taskId: string;
  /** The run to show instead of the task's details (`?run=`, P2-04). */
  runId?: string | undefined;
  laptop: boolean;
  onClose: () => void;
  onRun?: (runId: string | undefined) => void;
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
  // Switching between the details and a run (Run, Back to the task, or the browser's
  // history) removes the control that had focus; focus then falls to <body>, outside the
  // drawer, and Escape no longer reaches it. Put it back on the drawer, which takes
  // Escape, and leave it alone when it is anywhere else on purpose.
  useEffect(() => {
    const at = document.activeElement;
    if (at === null || at === document.body) panel.current?.focus();
  }, [runId]);
  // The task the drawer has open now (none once it closes): a Run answer for any other
  // task is late and is dropped.
  const openTask = useRef<string | undefined>(taskId);
  useEffect(() => {
    openTask.current = taskId;
    return () => {
      openTask.current = undefined;
    };
  }, [taskId]);
  const runFor = (run: string, forTask: string) => {
    if (forTask === openTask.current) onRun(run);
  };
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
        {runId !== undefined ? (
          <div className="flex min-h-0 flex-col gap-3">
            <button
              type="button"
              onClick={() => {
                onRun(undefined);
              }}
              className="min-h-11 self-start text-sm font-medium text-accent md:min-h-8"
            >
              Back to the task
            </button>
            <Suspense
              fallback={<p className="text-sm text-muted">Loading the run…</p>}
            >
              <RunView runId={runId} />
            </Suspense>
          </div>
        ) : data ? (
          <TaskDetails task={data} onClose={onClose} onRun={runFor} />
        ) : task.isError ? (
          <p className="text-sm text-muted">
            This task is not here any more; it may have moved to the trash.
          </p>
        ) : null}
      </div>
    </div>
  );
}
