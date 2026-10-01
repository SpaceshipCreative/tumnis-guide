// One item of the day's plan (P1-11, FR-1.2, FR-4.3, J1): the task, its project, label,
// estimate (Human and Hybrid only), first action, the plan's one-line reason and its time
// block (`Runs anytime` for AI work). Accept, Swap and Remove sit on the row; on the
// focused row `a`, `s` and `x` do the same. A task now waiting on the person keeps its
// place, marked `Waiting on you`, until Re-plan.
import type { KeyboardEvent } from "react";

import type { PlanItemViewOut } from "../../api/types.gen";
import { LABEL_TEXT, type Label } from "../common/LabelChip";
import { FirstActionLine } from "../common/FirstAction";
import { badge, BUTTON_QUIET } from "../common/ui";
import { clockTime } from "../../lib/time";
import { formatMinutes } from "./format";

export interface PlanItemActions {
  onAccept: () => void;
  onSwap: () => void;
  onRemove: () => void;
}

function blockText(item: PlanItemViewOut, timeZone: string): string {
  if (item.block === null) {
    return item.label === "ai" ? "Runs anytime" : "No time block";
  }
  return `${clockTime(item.block.start, timeZone)}–${clockTime(item.block.end, timeZone)}`;
}

const KEYS: Record<string, keyof PlanItemActions> = {
  a: "onAccept",
  s: "onSwap",
  x: "onRemove",
};

export function PlanItem({
  item,
  timeZone,
  ...actions
}: { item: PlanItemViewOut; timeZone: string } & PlanItemActions) {
  const label = item.label as Label | null;
  const estimate =
    (label === "human" || label === "hybrid") && item.estimate_minutes !== null
      ? item.estimate_minutes
      : null;
  const isAccepted = item.accepted_at !== null;
  const onKeyDown = (event: KeyboardEvent<HTMLLIElement>) => {
    if (event.target !== event.currentTarget) return; // a key inside a button is its own
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    const action = KEYS[event.key];
    if (action === undefined || (action === "onAccept" && isAccepted)) return;
    event.preventDefault();
    actions[action]();
  };
  return (
    <li
      tabIndex={0}
      aria-label={item.title}
      data-state={isAccepted ? "accepted" : "proposed"}
      onKeyDown={onKeyDown}
      className="flex flex-col gap-1 rounded-lg py-2 outline-offset-2 first:pt-0 last:pb-0 focus-visible:outline-2 focus-visible:outline-accent"
    >
      <div className="flex items-start justify-between gap-2">
        <span className="min-w-0 font-medium break-words">{item.title}</span>
        <span
          data-testid="plan-block"
          className="shrink-0 text-sm text-muted tabular-nums"
        >
          {blockText(item, timeZone)}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
        <span
          data-testid="plan-project"
          className="rounded bg-surface-muted px-1.5 py-0.5 text-text"
        >
          {item.project_name}
        </span>
        <span
          data-testid="label-chip"
          className="rounded border border-border px-1.5 py-0.5"
        >
          {label === null ? "No label yet" : LABEL_TEXT[label]}
        </span>
        {estimate !== null && (
          <span data-testid="estimate-chip">{formatMinutes(estimate)}</span>
        )}
        {item.blocked && (
          <span className={badge("warning")}>Waiting on you</span>
        )}
        {isAccepted && <span className={badge("success")}>Accepted</span>}
      </div>
      <p data-testid="plan-reason" className="text-sm">
        {item.reason}
      </p>
      <FirstActionLine task={{ first_action: item.first_action }} />
      <div className="flex flex-wrap gap-1">
        {!isAccepted && (
          <button
            type="button"
            className={BUTTON_QUIET}
            onClick={actions.onAccept}
            aria-keyshortcuts="a"
          >
            Accept
          </button>
        )}
        <button
          type="button"
          className={BUTTON_QUIET}
          onClick={actions.onSwap}
          aria-keyshortcuts="s"
        >
          Swap
        </button>
        <button
          type="button"
          className={BUTTON_QUIET}
          onClick={actions.onRemove}
          aria-keyshortcuts="x"
        >
          Remove
        </button>
      </div>
    </li>
  );
}
