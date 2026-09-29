// Settings > Working hours (P1-10, FR-4.7): Monday to Friday start and end, local times
// in the workspace timezone (09:00 to 18:00 until changed). The form validates with the
// generated zod schema plus one rule the schema cannot carry (the end comes after the
// start); a save sends the week in one PUT /v1/settings/working-hours with the version it
// read, and a 409 shows the current hours with a notice (REL-2).
import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { useForm } from "react-hook-form";
import * as z from "zod";

import { settingsGetWorkingHoursQueryKey } from "../../api/@tanstack/react-query.gen";
import type { WorkingHoursOut } from "../../api/types.gen";
import { zWorkingDay, zWorkingHoursOut } from "../../api/zod.gen";
import { ApiError, apiWrite, ConflictError, useWrite } from "../../lib/fetch";
import { workingHoursQuery } from "./queries";
import { BUTTON, ERROR, HEADING, HINT, INPUT, LABEL, SECTION } from "./styles";

const WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"];
const END_AFTER_START = "End must be after start";

// "HH:MM" strings compare in time order.
const weekSchema = z.object({
  days: z.array(
    zWorkingDay.refine((day) => day.end > day.start, {
      message: END_AFTER_START,
      path: ["end"],
    }),
  ),
});
type Week = z.infer<typeof weekSchema>;

/** The form path of one day's start or end. */
function field(index: number, key: "start" | "end") {
  return `days.${String(index)}.${key}` as `days.${number}.${typeof key}`;
}

/** Monday to Friday of the loaded week, in order (weekend rows are left as they are). */
function weekdays(loaded: WorkingHoursOut): Week {
  return {
    days: WEEKDAY_NAMES.map((_, weekday) => {
      const day = loaded.days.find((d) => d.weekday === weekday);
      return day ?? { weekday, start: "09:00", end: "18:00" };
    }),
  };
}

export function WorkingHoursSection() {
  const loaded = useQuery(workingHoursQuery());
  return (
    <section aria-labelledby="working-hours-title" className={SECTION}>
      <h2 id="working-hours-title" className={HEADING}>
        Working hours
      </h2>
      {loaded.data ? (
        <WorkingHoursForm loaded={loaded.data} />
      ) : loaded.isError ? (
        <p role="alert" className={ERROR}>
          The working hours could not be loaded.
        </p>
      ) : (
        <p className={HINT}>Loading…</p>
      )}
    </section>
  );
}

function WorkingHoursForm({ loaded }: { loaded: WorkingHoursOut }) {
  const queryClient = useQueryClient();
  const baseId = useId();
  const [version, setVersion] = useState(loaded.version);
  const [notice, setNotice] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const form = useForm<Week>({
    resolver: zodResolver(weekSchema),
    defaultValues: weekdays(loaded),
  });
  const { errors } = form.formState;

  function show(current: WorkingHoursOut) {
    queryClient.setQueryData(settingsGetWorkingHoursQueryKey(), current);
    setVersion(current.version);
    form.reset(weekdays(current));
  }

  const save = useWrite<
    Week & { version: number; idempotencyKey?: string },
    WorkingHoursOut
  >({
    mutationFn: ({ version: expected, idempotencyKey, ...body }) =>
      apiWrite({
        kind: "update",
        method: "PUT",
        path: "/settings/working-hours",
        body,
        version: expected,
        idempotencyKey,
        schema: zWorkingHoursOut,
      }),
    onSuccess: (current) => {
      show(current);
      setNotice("Saved.");
    },
    onError: (error) => {
      if (error instanceof ConflictError) {
        const current = zWorkingHoursOut.safeParse(error.current);
        if (current.success) {
          show(current.data);
          setNotice(
            "These hours changed elsewhere; you are now seeing the current values.",
          );
          return;
        }
      }
      setFailure(
        error instanceof ApiError
          ? (error.problem.detail ?? error.message)
          : "The working hours could not be saved.",
      );
    },
  });

  const submit = form.handleSubmit((week) => {
    setNotice(null);
    setFailure(null);
    save.mutate({ days: week.days, version });
  });

  return (
    <form
      noValidate
      onSubmit={(event) => void submit(event)}
      className="flex flex-col gap-4"
    >
      <p className={HINT}>
        Local times in the workspace timezone. Saturday and Sunday have no
        working hours unless you re-plan.
      </p>
      <ul className="flex flex-col gap-3">
        {WEEKDAY_NAMES.map((name, index) => {
          const startId = `${baseId}-${String(index)}-start`;
          const endId = `${baseId}-${String(index)}-end`;
          const errorId = `${endId}-error`;
          const endError = errors.days?.[index]?.end?.message;
          return (
            <li key={name}>
              <fieldset className="flex flex-col gap-2">
                <legend className="text-sm font-medium">{name}</legend>
                <div className="grid grid-cols-2 gap-2">
                  <div className={LABEL}>
                    <label htmlFor={startId}>Start</label>
                    <input
                      id={startId}
                      type="time"
                      aria-label={`${name} start`}
                      className={INPUT}
                      {...form.register(field(index, "start"))}
                    />
                  </div>
                  <div className={LABEL}>
                    <label htmlFor={endId}>End</label>
                    <input
                      id={endId}
                      type="time"
                      aria-label={`${name} end`}
                      className={INPUT}
                      aria-invalid={endError !== undefined}
                      aria-describedby={
                        endError === undefined ? undefined : errorId
                      }
                      {...form.register(field(index, "end"))}
                    />
                  </div>
                </div>
                {endError !== undefined && (
                  <p id={errorId} role="alert" className={ERROR}>
                    {endError}
                  </p>
                )}
              </fieldset>
            </li>
          );
        })}
      </ul>
      {failure !== null && (
        <p role="alert" className={ERROR}>
          {failure}
        </p>
      )}
      <p role="status" className={HINT}>
        {notice}
      </p>
      <div>
        <button type="submit" className={BUTTON} disabled={save.isPending}>
          Save
        </button>
      </div>
    </form>
  );
}
