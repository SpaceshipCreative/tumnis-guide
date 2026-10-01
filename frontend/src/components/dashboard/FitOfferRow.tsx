// A task the plan could not place (P1-11, J6): why, in plain words (`No 90-minute gap
// today`), and what it can do instead. Split shows the chunks that fit today's blocks
// (`Split 60 + 30`) and asks once more (`Accept split`) before it makes the subtasks;
// Move puts the task first on the first day with room (`Move to Tue 10 Mar`). The list of
// these rows is its own card, `Doesn't fit today`, shown only when there is one.
import { useState } from "react";

import type { PlanIssueOut } from "../../api/types.gen";
import { Card } from "../common/Card";
import { BUTTON_PRIMARY, BUTTON_SECONDARY, HINT } from "../common/ui";
import { usePlanAction } from "./planWrites";

/** `Tue 10 Mar` for a calendar day. */
function shortDay(day: string): string {
  const date = new Date(`${day}T00:00:00Z`);
  const weekday = new Intl.DateTimeFormat("en-GB", {
    weekday: "short",
    timeZone: "UTC",
  }).format(date);
  const dayMonth = new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  }).format(date);
  return `${weekday} ${dayMonth}`;
}

function why(issue: PlanIssueOut): string {
  if (issue.kind === "no_estimate" || issue.estimate_minutes === null) {
    return "No estimate yet, so it has no time today";
  }
  return `No ${String(issue.estimate_minutes)}-minute gap today`;
}

export function FitOfferRow({
  issue,
  day,
}: {
  issue: PlanIssueOut;
  day: string;
}) {
  const action = usePlanAction(day);
  const [confirming, setConfirming] = useState(false);
  const { split, move_to: moveTo } = issue.offer;
  const base = `/plan/${day}/issues/${issue.id}`;
  return (
    <li className="flex flex-col gap-2 py-2 first:pt-0 last:pb-0">
      <div>
        <p className="font-medium break-words">{issue.title}</p>
        <p className={HINT}>{why(issue)}</p>
      </div>
      <div className="flex flex-wrap gap-2">
        {split !== null &&
          (confirming ? (
            <>
              <button
                type="button"
                className={BUTTON_PRIMARY}
                disabled={action.isPending}
                onClick={() => {
                  action.mutate({ path: `${base}/split` });
                  setConfirming(false);
                }}
              >
                Accept split
              </button>
              <button
                type="button"
                className={BUTTON_SECONDARY}
                onClick={() => {
                  setConfirming(false);
                }}
              >
                Cancel
              </button>
            </>
          ) : (
            <button
              type="button"
              className={BUTTON_SECONDARY}
              disabled={action.isPending}
              onClick={() => {
                setConfirming(true);
              }}
            >
              Split {split.join(" + ")}
            </button>
          ))}
        {moveTo !== null && !confirming && (
          <button
            type="button"
            className={BUTTON_SECONDARY}
            disabled={action.isPending}
            onClick={() => {
              action.mutate({ path: `${base}/move` });
            }}
          >
            Move to {shortDay(moveTo)}
          </button>
        )}
      </div>
    </li>
  );
}

/** The plan's open issues, one row each; nothing when there are none. */
export function FitOfferList({
  issues,
  day,
  className = "",
}: {
  issues: readonly PlanIssueOut[];
  day: string;
  className?: string;
}) {
  const open = issues.filter((issue) => issue.resolved_at === null);
  if (open.length === 0) return null;
  return (
    <Card title="Doesn't fit today" className={className}>
      <ul className="flex flex-col divide-y divide-border">
        {open.map((issue) => (
          <FitOfferRow key={issue.id} issue={issue} day={day} />
        ))}
      </ul>
    </Card>
  );
}
