// Schedule (P0-24, FR-2.7, FR-3.5): the project's recurring tasks (`GET
// /v1/recurrence?project_id=`, P0-19); each opens its current task in the drawer. P4-04
// (FR-4.5): the unattended window in force for the project and where it comes from (its
// own, the workspace's, or none); the project can set its own or go back to the
// workspace's. It is read only when the section is open.
import { useId } from "react";
import { useQuery } from "@tanstack/react-query";

import type {
  RecurrenceOut,
  UnattendedWindowOut,
} from "../../../api/types.gen";
import { unattendedWindowQuery } from "../../settings/queries";
import {
  UnattendedWindowForm,
  windowWords,
} from "../../settings/UnattendedWindowForm";
import { projectRecurrenceQuery } from "../queries";
import { RailSection } from "./RailSection";

const PRESET_WORDS = {
  daily: "Daily",
  weekdays: "Weekdays",
  weekly: "Weekly",
  monthly: "Monthly",
} as const;

export function cadenceWords(rule: Pick<RecurrenceOut, "preset">): string {
  return rule.preset ? PRESET_WORDS[rule.preset] : "Custom schedule";
}

function sourceWords(loaded: UnattendedWindowOut): string {
  if (loaded.window === null) {
    return "No window: this project's AI tasks do not run unattended.";
  }
  const words = windowWords(loaded.window);
  return loaded.source === "project"
    ? `This project's own window: ${words}.`
    : `Uses the workspace window: ${words}.`;
}

function UnattendedWindow({ projectId }: { projectId: string }) {
  const loaded = useQuery(unattendedWindowQuery(projectId));
  const headingId = useId();
  const data = loaded.data;
  return (
    <section
      aria-labelledby={headingId}
      className="flex min-w-0 flex-col gap-2 border-t border-border pt-3"
    >
      <h3 id={headingId} className="text-sm font-medium">
        Unattended runs
      </h3>
      {data ? (
        <>
          <p className="text-sm text-muted">{sourceWords(data)}</p>
          <UnattendedWindowForm
            key={projectId}
            loaded={data}
            projectId={projectId}
            offText="Use the workspace window"
          />
        </>
      ) : (
        <p className="text-sm text-muted">
          {loaded.isError
            ? "The unattended window could not be loaded."
            : "Loading…"}
        </p>
      )}
    </section>
  );
}

export function ScheduleSection({
  projectId,
  open,
  onToggle,
  onOpenTask,
}: {
  projectId: string;
  open: boolean;
  onToggle: () => void;
  onOpenTask: (taskId: string) => void;
}) {
  const rules = useQuery(projectRecurrenceQuery(projectId));
  const n = rules.data?.length ?? 0;
  const summary = rules.isPending
    ? "Loading…"
    : rules.isError
      ? "Recurring tasks could not be loaded"
      : n === 0
        ? "No recurring tasks"
        : `${String(n)} recurring ${n === 1 ? "task" : "tasks"}`;
  return (
    <RailSection
      title="Schedule"
      summary={summary}
      open={open}
      onToggle={onToggle}
    >
      {n === 0 ? (
        <p className="text-sm text-muted">
          Set a task to repeat from its drawer.
        </p>
      ) : (
        <ul className="flex flex-col gap-1">
          {(rules.data ?? []).map((rule) => (
            <li key={rule.id}>
              <button
                type="button"
                disabled={rule.latest_task_id === null}
                onClick={() => {
                  if (rule.latest_task_id) onOpenTask(rule.latest_task_id);
                }}
                className="flex min-h-11 w-full items-center justify-between gap-2 rounded-md px-2 text-left text-sm hover:bg-surface-muted md:min-h-8"
              >
                <span className="min-w-0 truncate">{rule.title}</span>
                <span className="shrink-0 text-muted">
                  {cadenceWords(rule)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <UnattendedWindow projectId={projectId} />
    </RailSection>
  );
}
