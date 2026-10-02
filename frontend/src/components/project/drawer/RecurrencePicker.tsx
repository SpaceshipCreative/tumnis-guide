// The recurrence picker (P0-24, FR-3.5): never, daily, weekdays, weekly on a day, monthly
// on a date, at a local time; one `PUT /v1/tasks/{id}/recurrence` saves it with the
// version read (P0-19; the task becomes the first instance), `DELETE ?version=` stops it.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type ReactNode } from "react";

import { apiWrite, ConflictError, useWrite } from "../../../lib/fetch";
import { invalidateTaskViews } from "../../../lib/task-cache";
import { uiStore } from "../../../stores/uiStore";
import {
  projectRecurrenceQuery,
  taskRecurrenceQuery,
  type Preset,
  type RecurrenceRule,
} from "../queries";
import { fieldClass, saveClass } from "../rail/RailSection";
import type { Task } from "../types";

export const WEEKDAYS = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
] as const;

const DEFAULT_TIME = "09:00"; // (plan default) the working day's start

type Choice = Preset | "never";

/** The rule in plain words, e.g. "Repeats weekly on Monday at 09:00". */
export function describeRule(rule: RecurrenceRule | null | undefined): string {
  if (!rule) return "Does not repeat";
  const at = `at ${rule.due_time.slice(0, 5)}`;
  switch (rule.preset) {
    case "daily":
      return `Repeats daily ${at}`;
    case "weekdays":
      return `Repeats on weekdays ${at}`;
    case "weekly":
      return `Repeats weekly on ${WEEKDAYS[rule.weekday ?? 0] ?? "Monday"} ${at}`;
    case "monthly":
      return `Repeats monthly on day ${String(rule.month_day ?? 1)} ${at}`;
    case null:
      return `Repeats on a custom schedule (${rule.cron ?? "cron"})`;
  }
}

interface SaveVars {
  task: Pick<Task, "id" | "version">;
  rule: RecurrenceRule | null;
  choice: Choice;
  weekday: number;
  monthDay: number;
  time: string;
  idempotencyKey?: string;
}

function useSaveRecurrence() {
  const queryClient = useQueryClient();
  return useWrite<SaveVars, RecurrenceRule | null>({
    mutationFn: async (v) => {
      // P0-19: a new rule takes the task's version; an existing one its own.
      const version = v.rule?.version ?? v.task.version;
      if (v.choice === "never") {
        if (!v.rule) return null;
        await apiWrite({
          kind: "create",
          method: "DELETE",
          path: `/tasks/${v.task.id}/recurrence?version=${String(version)}`,
          idempotencyKey: v.idempotencyKey,
        });
        return null;
      }
      const body: Record<string, unknown> = { preset: v.choice };
      if (v.choice === "weekly") body.weekday = v.weekday;
      if (v.choice === "monthly") body.month_day = v.monthDay;
      body.due_time = v.time;
      return apiWrite<RecurrenceRule>({
        kind: "update",
        method: "PUT",
        path: `/tasks/${v.task.id}/recurrence`,
        body,
        version,
        idempotencyKey: v.idempotencyKey,
      });
    },
    onSuccess: (rule, v) => {
      queryClient.setQueryData(taskRecurrenceQuery(v.task.id).queryKey, rule);
    },
    onError: (error) => {
      uiStore.trigger.showNotice({
        text:
          error instanceof ConflictError
            ? "The task changed since; check the repeat and save again."
            : "Could not save the repeat. Try again.",
      });
    },
    onSettled: () => invalidateTaskViews(queryClient),
  });
}

function Field({
  id,
  label,
  children,
}: {
  id: string;
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1 text-sm">
      <label htmlFor={id}>{label}</label>
      {children}
    </div>
  );
}

function PickerForm({
  task,
  rule,
}: {
  task: Pick<Task, "id" | "version">;
  rule: RecurrenceRule | null;
}) {
  const [choice, setChoice] = useState<Choice>(rule?.preset ?? "never");
  const [weekday, setWeekday] = useState(rule?.weekday ?? 0);
  const [monthDay, setMonthDay] = useState(rule?.month_day ?? 1);
  const [time, setTime] = useState(rule?.due_time.slice(0, 5) ?? DEFAULT_TIME);
  const save = useSaveRecurrence();
  const id = useId();
  return (
    <form
      className="flex flex-col gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate({ task, rule, choice, weekday, monthDay, time });
      }}
    >
      <div className="flex flex-wrap gap-2">
        <Field id={`${id}-repeats`} label="Repeats">
          <select
            id={`${id}-repeats`}
            value={choice}
            onChange={(e) => {
              setChoice(e.target.value as Choice);
            }}
            className={fieldClass}
          >
            <option value="never">Never</option>
            <option value="daily">Daily</option>
            <option value="weekdays">Weekdays</option>
            <option value="weekly">Weekly</option>
            <option value="monthly">Monthly</option>
          </select>
        </Field>
        {choice === "weekly" && (
          <Field id={`${id}-day`} label="Day">
            <select
              id={`${id}-day`}
              value={String(weekday)}
              onChange={(e) => {
                setWeekday(Number(e.target.value));
              }}
              className={fieldClass}
            >
              {WEEKDAYS.map((day, i) => (
                <option key={day} value={String(i)}>
                  {day}
                </option>
              ))}
            </select>
          </Field>
        )}
        {choice === "monthly" && (
          <Field id={`${id}-month-day`} label="Day of month">
            <input
              id={`${id}-month-day`}
              type="number"
              min={1}
              max={31}
              value={monthDay}
              onChange={(e) => {
                setMonthDay(Number(e.target.value));
              }}
              className={fieldClass}
            />
          </Field>
        )}
        {choice !== "never" && (
          <Field id={`${id}-time`} label="Time">
            <input
              id={`${id}-time`}
              type="time"
              value={time}
              onChange={(e) => {
                setTime(e.target.value);
              }}
              className={fieldClass}
            />
          </Field>
        )}
      </div>
      <button type="submit" disabled={save.isPending} className={saveClass}>
        Save repeat
      </button>
    </form>
  );
}

export function RecurrencePicker({
  task,
}: {
  task: Pick<Task, "id" | "version" | "project_id">;
}) {
  // `GET /v1/tasks/{id}/recurrence` answers 404 for a task that does not repeat (P0-19),
  // a failed request on every drawer open (APP-09). The project's recurring tasks (the
  // Schedule rail's list) say whether the task is one; only then is its rule read. If the
  // list fails, the drawer asks for the rule anyway (one possible 404 beats hiding a
  // rule). A rule this drawer saved stays shown from the cache until the list catches up.
  const recurring = useQuery(projectRecurrenceQuery(task.project_id));
  // A list once loaded answers, even when a later refresh of it fails.
  const repeats =
    recurring.data !== undefined
      ? recurring.data.some((r) => r.latest_task_id === task.id)
      : recurring.isError;
  const rule = useQuery({ ...taskRecurrenceQuery(task.id), enabled: repeats });
  // A disabled query keeps its cached rule, so a rule read before another client stopped
  // the repeat would stay. A loaded list newer than that rule, without the task, has the
  // last word; a rule saved here (newer than the list) shows until the list catches up.
  const dropped =
    recurring.data !== undefined &&
    !repeats &&
    recurring.dataUpdatedAt > rule.dataUpdatedAt;
  // The form shows while the list loads, as it did while the rule loaded (T-P0-24-16): a
  // save in that moment is still safe, because the server applies a PUT to the task's
  // existing rule (P0-19), so it changes that rule rather than adding a second one.
  const current = dropped ? null : (rule.data ?? null);
  return (
    <section aria-label="Repeat" className="flex flex-col gap-2">
      <h3 className="text-sm font-semibold">Repeat</h3>
      <p className="text-sm text-muted">{describeRule(current)}</p>
      <PickerForm
        key={current ? `${current.id}:${String(current.version)}` : "none"}
        task={task}
        rule={current}
      />
    </section>
  );
}
