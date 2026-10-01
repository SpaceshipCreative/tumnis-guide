// The focus bar (P2-15, FR-10.3, FR-10.4, FR-10.9): on every signed-in page, under the
// header, while a task is in progress or Tumnis said something about focus today. It
// shows the task and its timer, the level in force, today's messages each with the level
// and rule that produced it, and one-tap replies to the latest check-in. The server owns
// cadence and firing; `GET /v1/focus/current` is the state (refreshed live over /ws) and
// the focusSession machine only mirrors it. Every reply is optional and the bar never
// blocks the page.
//
// At Guardrail (P4-01, FR-10.6, FR-10.9) "Less of this" stays one tap away, "Switched"
// opens the detour picker (the switch is captured as a task), and the server's return
// question shows until it is answered or counts as Stay after 2 minutes.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useActorRef, useSelector } from "@xstate/react";
import { useEffect, useState } from "react";

import {
  focusGetCurrentOptions,
  focusGetCurrentQueryKey,
} from "../../api/@tanstack/react-query.gen";
import type {
  DetourOut,
  FocusCurrentOut,
  FocusMessageOut,
} from "../../api/types.gen";
import { zFocusCurrentOut } from "../../api/zod.gen";
import { apiWrite, ConflictError, useWrite } from "../../lib/fetch";
import { LEVEL_TEXT } from "../../lib/levelRules";
import { taskQueryOptions } from "../../lib/optimistic";
import { invalidateTaskViews } from "../../lib/task-cache";
import {
  focusSession,
  type FocusSessionEvent,
} from "../../machines/focusSession";
import { uiStore } from "../../stores/uiStore";
import { BUTTON_QUIET, BUTTON_SECONDARY } from "../common/ui";
import { ReturnPrompt } from "./ReturnPrompt";
import { StuckPanel } from "./StuckPanel";
import { SwitchPicker } from "./SwitchPicker";

type Response = "still_on_it" | "switched" | "stuck" | "snooze";

/** The machine's reply events and the answer each posts. */
const RESPONSES: Partial<Record<FocusSessionEvent["type"], Response>> = {
  STILL_ON_IT: "still_on_it",
  SWITCHED: "switched",
  STUCK: "stuck",
  SNOOZE: "snooze",
};

const REPLIES: { label: string; event: FocusSessionEvent }[] = [
  { label: "Still on it", event: { type: "STILL_ON_IT" } },
  { label: "Switched", event: { type: "SWITCHED", toTaskId: "" } },
  { label: "Stuck", event: { type: "STUCK" } },
  { label: "Snooze", event: { type: "SNOOZE" } },
  { label: "Less of this", event: { type: "LESS_OF_THIS" } },
];

/** Whole minutes since `startedAt`, re-read every 30 seconds. */
function useMinutesSince(startedAt: string | undefined): number | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (startedAt === undefined) return;
    const tick = window.setInterval(() => {
      setNow(Date.now());
    }, 30_000);
    return () => {
      window.clearInterval(tick);
    };
  }, [startedAt]);
  if (startedAt === undefined) return null;
  return Math.max(0, Math.floor((now - Date.parse(startedAt)) / 60_000));
}

function Message({ message }: { message: FocusMessageOut }) {
  return (
    <li data-testid="focus-message" className="flex min-w-0 flex-col">
      <span className="min-w-0 break-words">{message.message}</span>
      <span data-testid="focus-attribution" className="text-xs text-muted">
        {message.rule}
      </span>
    </li>
  );
}

/** "Start" on a block's task: moves it to In progress (the session starts server side). */
function StartTask({ taskId }: { taskId: string }) {
  const queryClient = useQueryClient();
  const task = useQuery(taskQueryOptions(taskId));
  const start = useWrite<{ version: number; idempotencyKey?: string }, unknown>(
    {
      mutationFn: ({ version, idempotencyKey }) =>
        apiWrite({
          kind: "update",
          method: "POST",
          path: `/tasks/${taskId}/status`,
          body: { to: "in_progress" },
          version,
          idempotencyKey,
        }),
      onError: (error) => {
        if (error instanceof ConflictError) {
          uiStore.trigger.showConflict({ entity: "task" });
        }
      },
      onSettled: () => invalidateTaskViews(queryClient),
    },
  );
  const version = task.data?.version;
  return (
    <button
      type="button"
      className={BUTTON_SECONDARY}
      disabled={version === undefined || start.isPending}
      onClick={() => {
        if (version !== undefined) start.mutate({ version });
      }}
    >
      Start
    </button>
  );
}

export function FocusBar() {
  const queryClient = useQueryClient();
  const current = useQuery(focusGetCurrentOptions());
  const data = current.data;
  const session = data?.session ?? null;
  const messages = data?.messages ?? [];
  const latest = messages.at(-1);
  const pending = latest?.response === null ? latest : undefined;
  const minutes = useMinutesSince(session?.started_at);

  // A reply that failed reopens the check-in (the effect below sends its message
  // again) and says so; the next answer that lands clears the notice.
  const [failures, setFailures] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);
  const show = (answer: FocusCurrentOut) => {
    queryClient.setQueryData(focusGetCurrentQueryKey(), answer);
    setNotice(null);
  };
  const failed = () => {
    setNotice("Your answer was not saved. Try again.");
    setFailures((n) => n + 1);
  };
  const respond = useWrite<
    { event_id: string; response: Response; idempotencyKey?: string },
    FocusCurrentOut
  >({
    mutationFn: ({ idempotencyKey, ...body }) =>
      apiWrite({
        kind: "create",
        method: "POST",
        path: "/focus/respond",
        body,
        idempotencyKey,
        schema: zFocusCurrentOut,
      }),
    onSuccess: show,
    onError: failed,
  });
  const less = useWrite<
    { event_id?: string; idempotencyKey?: string },
    FocusCurrentOut
  >({
    mutationFn: ({ idempotencyKey, ...body }) =>
      apiWrite({
        kind: "create",
        method: "POST",
        path: "/focus/less",
        body,
        idempotencyKey,
        schema: zFocusCurrentOut,
      }),
    onSuccess: show,
    onError: failed,
  });

  // Guardrail (P4-01): a switch captured as a detour, and the answer to the return
  // question (`version` is the detour task's, read fresh when the answer is sent).
  const [picking, setPicking] = useState(false);
  const detour = data?.detour ?? null;
  const target = pending ?? latest;
  const capture = useWrite<
    {
      event_id: string;
      title: string;
      project_id: string;
      idempotencyKey?: string;
    },
    FocusCurrentOut
  >({
    mutationFn: ({ idempotencyKey, event_id, title, project_id }) =>
      apiWrite({
        kind: "create",
        method: "POST",
        path: "/focus/respond",
        body: { event_id, response: "switched", detour: { title, project_id } },
        idempotencyKey,
        schema: zFocusCurrentOut,
      }),
    onSuccess: (answer) => {
      show(answer);
      setPicking(false);
      void invalidateTaskViews(queryClient);
    },
    onError: () => {
      failed();
      actor.send({ type: "DISMISS" });
    },
  });
  const answerReturn = useWrite<
    { decision: "return" | "stay"; detour: DetourOut; idempotencyKey?: string },
    FocusCurrentOut
  >({
    mutationFn: async ({ decision, detour: open, idempotencyKey }) => {
      const task = await queryClient.query({
        ...taskQueryOptions(open.detour_task_id),
        staleTime: 0,
      });
      return apiWrite({
        kind: "create",
        method: "POST",
        path: "/focus/return",
        body: { decision, version: task.version, event_id: open.event_id },
        idempotencyKey,
        schema: zFocusCurrentOut,
      });
    },
    onSuccess: (answer) => {
      show(answer);
      void invalidateTaskViews(queryClient);
    },
    onError: failed,
  });

  // The implementations are given inline so they always see this render's message
  // (https://stately.ai/docs/migration: machine.provide() in the hook stays up to date).
  const actor = useActorRef(
    focusSession.provide({
      actions: {
        postResponse: ({ event }) => {
          const response = RESPONSES[event.type];
          if (pending && response) {
            respond.mutate({ event_id: pending.id, response });
          }
        },
        postLess: () => {
          if (pending) less.mutate({ event_id: pending.id });
        },
        postDetour: ({ event }) => {
          if (event.type === "SWITCH_DETOUR" && target) {
            capture.mutate({
              event_id: target.id,
              title: event.title,
              project_id: event.projectId,
            });
          }
        },
        postReturn: () => {
          if (detour) answerReturn.mutate({ decision: "return", detour });
        },
        postStay: () => {
          if (detour) answerReturn.mutate({ decision: "stay", detour });
        },
      },
    }),
  );
  const state = useSelector(actor, (s) => s.value);

  // The server's state drives the machine: the level, the session, the latest message
  // and an open return question.
  const level = data?.level;
  useEffect(() => {
    if (level !== undefined) actor.send({ type: "LEVEL", level });
  }, [actor, level]);
  const sessionTask = session?.task_id;
  const sessionStart = session?.started_at;
  useEffect(() => {
    if (sessionTask !== undefined && sessionStart !== undefined) {
      actor.send({
        type: "TASK_STARTED",
        taskId: sessionTask,
        at: Date.parse(sessionStart),
      });
    } else {
      actor.send({
        type: "TASK_LEFT",
        taskId: actor.getSnapshot().context.taskId ?? "",
      });
    }
  }, [actor, sessionTask, sessionStart]);
  const pendingId = pending?.id;
  useEffect(() => {
    if (pending === undefined) return;
    actor.send({
      type: "FOCUS_EVENT",
      kind: pending.kind,
      taskId: pending.task_id ?? "",
      level: pending.level,
      rule: pending.rule,
      message: pending.message,
    });
    // Only a new message (or a failed reply to this one) is a new event; the rest of
    // `pending` is the same message.
  }, [actor, pendingId, failures]);
  const detourId = detour?.event_id;
  useEffect(() => {
    if (detour === null) return;
    actor.send({
      type: "RETURN_PROMPT",
      detourTaskId: detour.detour_task_id,
      returnToTaskId: detour.return_to_task_id ?? "",
    });
    // A new open question (or a failed answer to this one) is a new event; the rest of
    // `detour` is the same question.
  }, [actor, detourId, failures]);

  if (!data || (session === null && messages.length === 0)) return null;

  const canStart =
    pending !== undefined &&
    pending.task_id !== null &&
    (pending.kind === "block_start" || pending.kind === "not_started") &&
    pending.task_id !== session?.task_id;
  const earlier = messages.slice(0, -1);
  const guardrail = data.level === "guardrail";
  const asking = state === "returnPrompt" && detour !== null;
  const switchedAt = (open: boolean) => {
    if (guardrail) {
      setPicking(open);
    } else if (open) {
      actor.send({ type: "SWITCHED", toTaskId: sessionTask ?? "" });
    }
  };

  return (
    <section
      aria-label="Focus"
      className="flex min-w-0 flex-col gap-2 border-b border-border bg-surface px-4 py-2 md:px-8"
    >
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-sm">
        {session !== null && (
          <>
            <span className="min-w-0 truncate font-medium">
              {session.title}
            </span>
            <span className="text-muted tabular-nums">{`${String(minutes ?? 0)} min`}</span>
          </>
        )}
        <span
          data-testid="focus-level"
          className="ml-auto rounded border border-border px-1.5 py-0.5 text-xs"
        >
          {LEVEL_TEXT[data.level]}
          {data.override_level !== null ? " today" : ""}
        </span>
      </div>
      {earlier.length > 0 && (
        <details className="text-sm">
          <summary className={`${BUTTON_QUIET} cursor-pointer`}>
            {`Earlier today (${String(earlier.length)})`}
          </summary>
          <ul className="flex flex-col gap-1 pt-1">
            {earlier.map((m) => (
              <Message key={m.id} message={m} />
            ))}
          </ul>
        </details>
      )}
      {latest !== undefined && (
        <ul className="text-sm">
          <Message message={latest} />
        </ul>
      )}
      {data.next_step && <StuckPanel step={data.next_step} />}
      {notice !== null && (
        <p role="alert" className="text-sm text-danger">
          {notice}
        </p>
      )}
      {asking && (
        <ReturnPrompt
          detour={detour}
          busy={answerReturn.isPending}
          onReturn={() => {
            actor.send({ type: "RETURN" });
          }}
          onStay={() => {
            actor.send({ type: "STAY" });
          }}
        />
      )}
      {picking && guardrail && target !== undefined && (
        <SwitchPicker
          busy={capture.isPending}
          onCapture={(title, projectId) => {
            actor.send({ type: "SWITCH_DETOUR", title, projectId });
          }}
          onCancel={() => {
            setPicking(false);
          }}
        />
      )}
      {guardrail && state !== "checkIn" && !asking && !picking && (
        <div className="flex flex-wrap gap-2">
          {target !== undefined && state === "active" && (
            <button
              type="button"
              className={BUTTON_SECONDARY}
              onClick={() => {
                switchedAt(true);
              }}
            >
              Switched
            </button>
          )}
          <button
            type="button"
            className={BUTTON_SECONDARY}
            disabled={less.isPending}
            onClick={() => {
              less.mutate({});
            }}
          >
            Less of this
          </button>
        </div>
      )}
      {(state === "checkIn" || canStart) && pending !== undefined && (
        <div className="flex flex-wrap gap-2">
          {canStart && pending.task_id !== null && (
            <StartTask taskId={pending.task_id} />
          )}
          {state === "checkIn" &&
            REPLIES.map((reply) => (
              <button
                key={reply.label}
                type="button"
                className={BUTTON_SECONDARY}
                disabled={respond.isPending || less.isPending}
                onClick={() => {
                  if (reply.event.type === "SWITCHED") switchedAt(true);
                  else actor.send(reply.event);
                }}
              >
                {reply.label}
              </button>
            ))}
        </div>
      )}
    </section>
  );
}
