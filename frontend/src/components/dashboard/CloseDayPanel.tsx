// Close the day (P1-18, J7): an optional one-minute look back. Four sections (Shipped,
// Agents finished, Queued overnight, Rolls over), each a card that says so when it is
// empty. Rollover counts are plain numbers in the body colour (UX 8: no warning colour,
// no shame). Nothing here asks for input: Done or Escape closes the panel and writes
// nothing, so skipping the day close costs nothing. Closing it gives focus back to what
// opened it. Queued overnight lists the tasks queued to run unattended (P4-04), each that
// will not run with its reason. On a phone the panel is a full-height
// sheet; on a laptop a centred dialog.
import { queryOptions, useQuery } from "@tanstack/react-query";
import { useEffect, useId, useRef, type ReactNode } from "react";

import { planningGetDaySummaryOptions } from "../../api/@tanstack/react-query.gen";
import type {
  QueuedUnattendedOut,
  RolloverRef,
  TaskRef,
} from "../../api/types.gen";
import { dialogKeyDown } from "../../lib/focusTrap";
import { Card } from "../common/Card";
import { BUTTON_PRIMARY, DIALOG_TITLE, HINT } from "../common/ui";

/** The day's summary, read fresh each time the panel opens (work may have just ended). */
export function daySummaryQuery(day: string) {
  return queryOptions({
    ...planningGetDaySummaryOptions({ path: { day } }),
    staleTime: 0,
  });
}

const LIST = "flex flex-col divide-y divide-border";
const ROW = "flex flex-col gap-0.5 py-2 first:pt-0 last:pb-0";

function TaskList({
  tasks,
  empty,
}: {
  tasks: readonly TaskRef[];
  empty: string;
}) {
  if (tasks.length === 0) return <p className={HINT}>{empty}</p>;
  return (
    <ul className={LIST}>
      {tasks.map((task) => (
        <li key={task.task_id} className={ROW}>
          <span className="break-words">{task.title}</span>
        </li>
      ))}
    </ul>
  );
}

/** The tasks queued to run tonight, in queued order (P4-04, J7): each says when it will
 * not run, and why, in plain words. */
function QueuedList({
  tasks,
}: {
  tasks: readonly (QueuedUnattendedOut | TaskRef)[];
}) {
  if (tasks.length === 0) return <p className={HINT}>Nothing queued</p>;
  return (
    <ul className={LIST}>
      {tasks.map((task) => (
        <li key={task.task_id} className={ROW}>
          <span className="break-words">{task.title}</span>
          {"will_run" in task && !task.will_run && (
            <span className="text-sm text-muted">
              Will not run
              {task.reason === null ? "" : `: ${task.reason}`}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

function RolloverList({ tasks }: { tasks: readonly RolloverRef[] }) {
  if (tasks.length === 0) return <p className={HINT}>Nothing rolls over</p>;
  return (
    <ul className={LIST}>
      {tasks.map((task) => (
        <li key={task.task_id} className={ROW}>
          <span className="break-words">{task.title}</span>
          <span className="text-sm text-muted">
            Rolled over{" "}
            <span data-testid="rollover-count" className="tabular-nums">
              {task.rollover_count}
            </span>{" "}
            · +1 tonight
          </span>
        </li>
      ))}
    </ul>
  );
}

function prepared(count: number): string {
  return `${String(count)} ${count === 1 ? "task" : "tasks"} prepared by agents`;
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card title={title} headingLevel={3}>
      {children}
    </Card>
  );
}

export function CloseDayPanel({
  day,
  onClose,
}: {
  /** The workspace's local day, `YYYY-MM-DD`. */
  day: string;
  onClose: () => void;
}) {
  const titleId = useId();
  const done = useRef<HTMLButtonElement>(null);
  const summary = useQuery(daySummaryQuery(day));
  useEffect(() => {
    // Whatever had focus opened the panel; it gets focus back when the panel goes, unless
    // something else has taken it by then.
    const opener =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    done.current?.focus();
    return () => {
      const lost =
        document.activeElement === null ||
        document.activeElement === document.body;
      if (lost && opener?.isConnected) opener.focus();
    };
  }, []);

  const data = summary.data;
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-gray-900/40 sm:items-center sm:p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="flex h-dvh w-full flex-col gap-3 overflow-y-auto bg-surface p-4 shadow-lg sm:h-auto sm:max-h-[90dvh] sm:max-w-lg sm:rounded-xl sm:border sm:border-border sm:p-5"
        onKeyDown={(event) => {
          dialogKeyDown(event, onClose);
        }}
      >
        <h2 id={titleId} className={DIALOG_TITLE}>
          Close the day
        </h2>
        {data === undefined ? (
          <p className={HINT} role="status">
            {summary.isError
              ? "The day's summary is unavailable right now."
              : "Loading the day…"}
          </p>
        ) : (
          <>
            <Section title="Shipped">
              <TaskList tasks={data.shipped} empty="Nothing shipped today" />
            </Section>
            <Section title="Agents finished">
              {data.agents_finished.length > 0 ||
              data.prepared_by_agents > 0 ? (
                <>
                  {data.agents_finished.length > 0 && (
                    <TaskList tasks={data.agents_finished} empty="" />
                  )}
                  {data.prepared_by_agents > 0 && (
                    <p className={HINT}>{prepared(data.prepared_by_agents)}</p>
                  )}
                </>
              ) : (
                <p className={HINT}>No agent work finished today</p>
              )}
            </Section>
            <Section title="Queued overnight">
              <QueuedList
                tasks={data.queued_unattended ?? data.queued_overnight}
              />
            </Section>
            <Section title="Rolls over">
              <RolloverList tasks={data.rolls_over} />
            </Section>
          </>
        )}
        <div className="mt-auto flex justify-end sm:mt-0">
          <button
            ref={done}
            type="button"
            className={BUTTON_PRIMARY}
            onClick={onClose}
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
