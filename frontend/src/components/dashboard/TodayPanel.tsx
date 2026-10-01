// The Today panel (P0-23, FR-1.2): at most five tasks in the server's today order, then
// "+N more" to the full list on /tasks. A card (DS-01): the "+N more" link sits in its
// header row, and on a laptop the list scrolls inside the card, never the page.
//
// With `planDay` (P1-11, R-16) it reads that day's published plan instead: at most five
// of its items in plan order, each with its reason and time block, Accept, Swap and
// Remove, and a plain notice when the plan was made by due date. Re-plan, in the header,
// rebuilds the day on request; nothing re-plans on its own. A day without a plan shows the
// Today tasks as before.
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";

import type { PlanItemViewOut, PlanOut } from "../../api/types.gen";
import { Card } from "../common/Card";
import { BUTTON_QUIET } from "../common/ui";
import { PlanItem } from "./PlanItem";
import { accepted, removed, usePlanAction, useReplan } from "./planWrites";
import { planQuery } from "./queries";
import { SwapPicker } from "./SwapPicker";
import { TodayItem } from "./TodayItem";
import type { TodayTask } from "./types";

export const TODAY_LIMIT = 5;

/** The plain words for a plan's notice (why it was made by due date). */
export const NOTICE_TEXT: Readonly<Record<string, string>> = {
  agent_offline: "Planned by due date: your planning agent is offline",
  invalid_plan:
    "Planned by due date: your planning agent's plan did not pass the checks",
};

function shownItems(plan: PlanOut): PlanItemViewOut[] {
  return plan.items
    .filter((item) => item.removed_at === null)
    .sort((a, b) => a.position - b.position)
    .slice(0, TODAY_LIMIT);
}

function PlanList({ plan }: { plan: PlanOut }) {
  const action = usePlanAction(plan.day);
  const [swapping, setSwapping] = useState<PlanItemViewOut | null>(null);
  const items = shownItems(plan);
  const base = `/plan/${plan.day}/items`;
  const notice = plan.notice === null ? null : NOTICE_TEXT[plan.notice];
  return (
    <>
      {notice && <p className="text-sm text-muted">{notice}</p>}
      {items.length === 0 ? (
        <p className="text-sm text-muted">Nothing planned for today.</p>
      ) : (
        <ol className="flex flex-col divide-y divide-border">
          {items.map((item) => (
            <PlanItem
              key={item.id}
              item={item}
              timeZone={plan.timezone}
              onAccept={() => {
                action.mutate({
                  path: `${base}/${item.task_id}/accept`,
                  optimistic: accepted(item.task_id),
                });
              }}
              onSwap={() => {
                setSwapping(item);
              }}
              onRemove={() => {
                action.mutate({
                  path: `${base}/${item.task_id}/remove`,
                  optimistic: removed(item.task_id),
                });
              }}
            />
          ))}
        </ol>
      )}
      {swapping && (
        <SwapPicker
          day={plan.day}
          title={swapping.title}
          onClose={() => {
            setSwapping(null);
          }}
          onPick={(alternate) => {
            action.mutate({
              path: `${base}/${swapping.task_id}/swap`,
              body: { with_task_id: alternate.id },
            });
            setSwapping(null);
          }}
        />
      )}
    </>
  );
}

function ReplanButton({ day }: { day: string }) {
  const replan = useReplan(day);
  return (
    <button
      type="button"
      className={`${BUTTON_QUIET} shrink-0`}
      disabled={replan.isPending}
      onClick={() => {
        replan.mutate({});
      }}
    >
      {replan.isPending ? "Re-planning…" : "Re-plan"}
    </button>
  );
}

function TaskList({
  items,
  projectNames,
}: {
  items: readonly TodayTask[];
  projectNames: Readonly<Record<string, string>>;
}) {
  return (
    <ol className="flex flex-col divide-y divide-border">
      {items.map((task) => (
        <TodayItem
          key={task.id}
          task={task}
          projectName={projectNames[task.project_id] ?? "Unknown project"}
        />
      ))}
    </ol>
  );
}

export function TodayPanel({
  items,
  total,
  projectNames,
  unavailable = false,
  pending = false,
  planDay,
  className = "",
}: {
  items: readonly TodayTask[];
  total: number;
  projectNames: Readonly<Record<string, string>>;
  /** The Today query failed: say so instead of claiming an empty day. */
  unavailable?: boolean;
  /** No answer yet (a retry after a failure): claim nothing. */
  pending?: boolean;
  /** The workspace's local day (`YYYY-MM-DD`): its published plan shows instead (P1-11). */
  planDay?: string;
  className?: string;
}) {
  const plan = useQuery({
    ...planQuery(planDay ?? ""),
    enabled: planDay !== undefined,
  });
  const planned = plan.data;
  const shown = items.slice(0, TODAY_LIMIT);
  const more = planned ? 0 : Math.max(0, total - shown.length);
  const waiting = pending || (planDay !== undefined && plan.isPending);
  return (
    <Card
      title="Today"
      className={`min-h-0 ${className}`}
      bodyClassName="flex min-h-0 min-w-0 flex-1 flex-col gap-2 px-4 py-2 md:overflow-y-auto"
      action={
        <div className="flex shrink-0 items-center gap-2">
          {more > 0 && (
            <Link
              to="/tasks"
              search={{ status: "today" }}
              className="shrink-0 text-sm font-medium text-accent hover:underline"
            >
              +{more} more
            </Link>
          )}
          {planDay !== undefined && <ReplanButton day={planDay} />}
        </div>
      }
    >
      {waiting ? null : planned ? (
        <PlanList plan={planned} />
      ) : unavailable ? (
        <p className="text-sm text-muted">Today's tasks could not be loaded.</p>
      ) : shown.length === 0 ? (
        <p className="text-sm text-muted">Nothing planned for today.</p>
      ) : (
        <TaskList items={shown} projectNames={projectNames} />
      )}
    </Card>
  );
}
