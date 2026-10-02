// The stuck panel (P4-02, FR-10.5): after "Stuck", the project agent works on a first
// step. The panel says so, then shows what came back: the subtask it split off with its
// minutes, its report when it took the step itself, or the fallback (the task's first
// action with a 10-minute timer) when nothing arrived in time. Once the person reviews
// the agent's report (Scott decision 73): "Step done" when they accepted it, or "Back to
// you" with the task's first action when they rejected it. `GET /v1/focus/current`
// carries the state as `next_step`, refreshed over /ws, so one live region changes its
// content and screen readers hear each change.
import { useEffect, useState } from "react";

import type { NextStepOut } from "../../api/types.gen";

/** Whole minutes left on a `minutes` timer started at `startedAt`, re-read every 30 s. */
function useMinutesLeft(startedAt: string | null, minutes: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (startedAt === null) return;
    const tick = window.setInterval(() => {
      setNow(Date.now());
    }, 30_000);
    return () => {
      window.clearInterval(tick);
    };
  }, [startedAt]);
  if (startedAt === null) return minutes;
  const spent = Math.floor((now - Date.parse(startedAt)) / 60_000);
  return Math.min(minutes, Math.max(0, minutes - spent));
}

function Content({ step }: { step: NextStepOut }) {
  const left = useMinutesLeft(step.fallback_at, step.timer_minutes ?? 10);
  switch (step.state) {
    case "working":
      return <span className="text-muted">Working on a first step…</span>;
    case "split":
      return step.step === null ? null : (
        <>
          <span className="min-w-0 break-words font-medium">
            {step.step.title}
          </span>
          {step.step.estimate_minutes !== null && (
            <span className="text-muted tabular-nums">
              {`${String(step.step.estimate_minutes)} min`}
            </span>
          )}
        </>
      );
    case "took_step":
      return (
        <span className="min-w-0 break-words">
          {step.summary ?? "The agent took the next step."}
        </span>
      );
    case "done":
      return (
        <>
          <span className="text-xs text-muted">Step done</span>
          <span className="min-w-0 break-words">
            {step.summary ?? "The agent took the next step."}
          </span>
        </>
      );
    case "reopened":
      return (
        <>
          <span className="text-xs text-muted">Back to you</span>
          <span className="min-w-0 break-words font-medium">
            {step.first_action ?? "Pick the smallest next step"}
          </span>
        </>
      );
    case "fallback":
      return (
        <>
          <span className="text-xs text-muted">Agent still working</span>
          <span className="min-w-0 break-words font-medium">
            {step.first_action ?? "Pick the smallest next step"}
          </span>
          <span className="text-muted tabular-nums">
            {`${String(left)} min left`}
          </span>
        </>
      );
  }
}

export function StuckPanel({ step }: { step: NextStepOut }) {
  return (
    <div
      role="status"
      aria-label="Next step"
      className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1 text-sm"
    >
      <Content step={step} />
    </div>
  );
}
