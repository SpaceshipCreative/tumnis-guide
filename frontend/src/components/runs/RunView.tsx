// The run view (P2-04, FR-5.5, UX 10): one run of an agent as it happens. Its log (the
// lines, tool calls and files the agent streams, in order, read by cursor pages through
// useRunEvents), how long it has run, and a Stop button that asks the server to cancel
// the run (`POST /v1/runs/{id}/cancel`, 202; the worker stops the agent). A card (DS-01,
// ADR-0012) whose log scrolls inside it, so the Stop button stays in reach on the phone.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import {
  agentsGetRunOptions,
  agentsGetRunQueryKey,
} from "../../api/@tanstack/react-query.gen";
import type { RunEventOut, RunOut } from "../../api/types.gen";
import { ApiError, apiWrite, useWrite } from "../../lib/fetch";
import { useRunEvents } from "../../lib/useRunEvents";
import { badge, BUTTON_DANGER, CARD, CARD_HEADER, HINT } from "../common/ui";
import type { BadgeTone } from "../common/ui";

/** How often an active run's status is read again (a /ws notice reads it sooner). */
const RUN_POLL_MS = 5_000;
const ACTIVE = new Set(["queued", "running", "waiting_on_human", "held"]);
const LINE_KINDS = new Set(["log", "tool_call", "file"]);
/** The elapsed time counts in steps of this many seconds: a calm header while lines
 * stream in, and an honest figure at the minute scale a run is judged on. */
const ELAPSED_STEP_S = 5;

const STATUS: Record<string, { text: string; tone: BadgeTone }> = {
  queued: { text: "Queued", tone: "neutral" },
  held: { text: "Held", tone: "warning" },
  running: { text: "Running", tone: "info" },
  waiting_on_human: { text: "Waiting on you", tone: "warning" },
  succeeded: { text: "Finished", tone: "success" },
  failed: { text: "Failed", tone: "danger" },
  cancelled: { text: "Stopped", tone: "neutral" },
  timed_out: { text: "Stopped at the limit", tone: "warning" },
  runner_lost: { text: "Runner lost", tone: "danger" },
};

/** m:ss (or h:mm:ss) of a number of seconds. */
export function formatElapsed(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = String(seconds % 60).padStart(2, "0");
  return h > 0
    ? `${String(h)}:${String(m).padStart(2, "0")}:${s}`
    : `${String(m)}:${s}`;
}

function useNow(ticking: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!ticking) return;
    const timer = setInterval(() => {
      setNow(Date.now());
    }, 1_000);
    return () => {
      clearInterval(timer);
    };
  }, [ticking]);
  return now;
}

function Elapsed({ run, active }: { run: RunOut; active: boolean }) {
  const now = useNow(active);
  if (run.started_at === null) {
    return <span className={HINT}>Not started</span>;
  }
  const end =
    active || run.finished_at === null ? now : Date.parse(run.finished_at);
  const raw = (end - Date.parse(run.started_at)) / 1000;
  const seconds = Math.floor(raw / ELAPSED_STEP_S) * ELAPSED_STEP_S;
  return (
    <span aria-label="Elapsed" className="font-mono text-sm tabular-nums">
      {formatElapsed(seconds)}
    </span>
  );
}

function lineText(event: RunEventOut): string {
  const text = event.payload.text;
  return typeof text === "string" ? text : "";
}

const LINE_STYLE: Record<string, string> = {
  log: "",
  tool_call: "text-info",
  file: "text-muted",
};

function Line({ event }: { event: RunEventOut }) {
  const system = event.payload.source === "system";
  return (
    <li
      className={`px-3 py-0.5 break-words whitespace-pre-wrap ${
        system ? "font-semibold" : (LINE_STYLE[event.kind] ?? "")
      }`}
    >
      {event.kind === "tool_call" && <span aria-hidden="true">› </span>}
      {lineText(event)}
    </li>
  );
}

export function RunView({ runId }: { runId: string }) {
  const queryClient = useQueryClient();
  const [message, setMessage] = useState<string | null>(null);
  const run = useQuery({
    ...agentsGetRunOptions({ path: { run_id: runId } }),
    refetchInterval: (query) =>
      query.state.data === undefined || ACTIVE.has(query.state.data.status)
        ? RUN_POLL_MS
        : false,
  });
  const active = run.data === undefined || ACTIVE.has(run.data.status);
  const events = useRunEvents(runId, { active });
  const lines = (events.data ?? []).filter((e) => LINE_KINDS.has(e.kind));

  const stop = useWrite<{ idempotencyKey?: string }, unknown>({
    mutationFn: ({ idempotencyKey }) =>
      apiWrite<unknown>({
        kind: "create",
        method: "POST",
        path: `/runs/${encodeURIComponent(runId)}/cancel`,
        idempotencyKey,
      }),
    onSuccess: () => {
      setMessage("Stopping the run.");
    },
    onError: (error) => {
      setMessage(
        error instanceof ApiError
          ? (error.problem.detail ?? error.message)
          : "The run could not be stopped.",
      );
    },
    onSettled: () => {
      void queryClient.invalidateQueries({
        queryKey: agentsGetRunQueryKey({ path: { run_id: runId } }),
      });
    },
  });

  const status = run.data === undefined ? undefined : STATUS[run.data.status];
  return (
    <section aria-label="Run" className={`${CARD} max-h-[80dvh]`}>
      <header className={`${CARD_HEADER} flex-wrap`}>
        <div className="flex min-w-0 items-center gap-2">
          <h2 className="text-base font-semibold">Run</h2>
          {status !== undefined && (
            <span className={badge(status.tone)}>{status.text}</span>
          )}
          {run.data !== undefined && <Elapsed run={run.data} active={active} />}
        </div>
        {active && (
          <button
            type="button"
            className={BUTTON_DANGER}
            disabled={stop.isPending || run.data === undefined}
            onClick={() => {
              stop.mutate({});
            }}
          >
            Stop
          </button>
        )}
      </header>
      <p role="status" className="px-4 pt-2 text-sm text-muted empty:hidden">
        {message}
      </p>
      {run.isError && (
        <p role="alert" className="px-4 py-2 text-sm text-danger">
          This run could not be loaded.
        </p>
      )}
      <ol
        role="log"
        aria-label="Run log"
        aria-live="polite"
        className="min-h-24 flex-1 overflow-y-auto py-2 font-mono text-xs"
      >
        {lines.map((event) => (
          <Line key={event.message_id} event={event} />
        ))}
      </ol>
      {events.isSuccess && lines.length === 0 && (
        <p className={`${HINT} px-4 pb-3`}>No output yet.</p>
      )}
    </section>
  );
}
