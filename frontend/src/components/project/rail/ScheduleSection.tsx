// Schedule (P0-24, FR-2.7, FR-3.5): the project's recurring tasks (`GET
// /v1/recurrence?project_id=`, P0-19); each opens its current task in the drawer.
import { useQuery } from "@tanstack/react-query";

import { projectRecurrenceQuery, type RecurrenceRule } from "../queries";
import { RailSection } from "./RailSection";

const PRESET_WORDS = {
  daily: "Daily",
  weekdays: "Weekdays",
  weekly: "Weekly",
  monthly: "Monthly",
} as const;

export function cadenceWords(rule: RecurrenceRule): string {
  return rule.preset ? PRESET_WORDS[rule.preset] : "Custom schedule";
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
                onClick={() => {
                  onOpenTask(rule.latest_task_id ?? rule.task_id);
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
    </RailSection>
  );
}
