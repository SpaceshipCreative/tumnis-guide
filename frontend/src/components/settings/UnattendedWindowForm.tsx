// The unattended window form (P4-04, FR-4.5), shared by Settings > Unattended runs (the
// workspace's window) and a project's Schedule rail (its own window): the nights it opens
// (Monday to Sunday), a start and an end in the workspace timezone (an end before the start
// runs overnight), Save and a way to turn it off. A save sends `PUT /v1/unattended/window`
// with the version it read (null when the scope stores no window yet); a 409 shows the
// current window with a notice (REL-2).
import { useQueryClient } from "@tanstack/react-query";
import { useId, useState, type SyntheticEvent } from "react";

import { planningGetUnattendedWindowQueryKey } from "../../api/@tanstack/react-query.gen";
import type { UnattendedWindowOut, WindowSpec } from "../../api/types.gen";
import { zUnattendedWindowOut } from "../../api/zod.gen";
import { ApiError, apiWrite, ConflictError, useWrite } from "../../lib/fetch";
import {
  BUTTON_PRIMARY,
  BUTTON_SECONDARY,
  ERROR_TEXT,
  FIELD,
  FIELD_LABEL,
  HINT,
} from "../common/ui";

const DAYS = [
  ["Mon", "Monday"],
  ["Tue", "Tuesday"],
  ["Wed", "Wednesday"],
  ["Thu", "Thursday"],
  ["Fri", "Friday"],
  ["Sat", "Saturday"],
  ["Sun", "Sunday"],
] as const;

/** Monday to Friday, 22:00 to 06:00: what the form offers before any window is set. */
const FIRST_WINDOW = {
  weekdays: [0, 1, 2, 3, 4],
  start: "22:00",
  end: "06:00",
};

/** "HH:MM" of the API's "HH:MM:SS" (or "HH:MM"). */
function hhmm(local: string): string {
  return local.slice(0, 5);
}

/** The window in plain words: "Mon, Tue, Wed 22:00 to 06:00 (overnight)". */
export function windowWords(window: WindowSpec): string {
  const days =
    window.weekdays.length === 7
      ? "Every day"
      : [...window.weekdays]
          .sort((a, b) => a - b)
          .map((day) => DAYS[day]?.[0] ?? String(day))
          .join(", ");
  const start = hhmm(window.start_local);
  const end = hhmm(window.end_local);
  return `${days}, ${start} to ${end}${end < start ? " (overnight)" : ""}`;
}

interface Fields {
  weekdays: number[];
  start: string;
  end: string;
}

function fieldsOf(loaded: UnattendedWindowOut): Fields {
  const window = loaded.window;
  return window === null
    ? FIRST_WINDOW
    : {
        weekdays: window.weekdays,
        start: hhmm(window.start_local),
        end: hhmm(window.end_local),
      };
}

interface Save {
  window: WindowSpec | null;
  version: number | null;
  idempotencyKey?: string;
}

export function UnattendedWindowForm({
  loaded,
  projectId,
  offText,
}: {
  loaded: UnattendedWindowOut;
  /** The project whose own window this edits; null for the workspace's. */
  projectId: string | null;
  /** The button that removes this scope's window. */
  offText: string;
}) {
  const queryClient = useQueryClient();
  const baseId = useId();
  const [current, setCurrent] = useState(loaded);
  const [fields, setFields] = useState<Fields>(() => fieldsOf(loaded));
  const [notice, setNotice] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  function show(next: UnattendedWindowOut) {
    queryClient.setQueryData(
      planningGetUnattendedWindowQueryKey(
        projectId === null ? undefined : { query: { project_id: projectId } },
      ),
      next,
    );
    setCurrent(next);
    setFields(fieldsOf(next));
  }

  const save = useWrite<Save, UnattendedWindowOut>({
    mutationFn: ({ window, version, idempotencyKey }) =>
      apiWrite({
        kind: "create",
        method: "PUT",
        path: "/unattended/window",
        body: { project_id: projectId, window, version },
        idempotencyKey,
        schema: zUnattendedWindowOut,
      }),
    onSuccess: (next, { window }) => {
      show(next);
      setNotice(window === null ? "Turned off." : "Saved.");
    },
    onError: (error) => {
      if (error instanceof ConflictError) {
        const latest = zUnattendedWindowOut.safeParse(error.current);
        if (latest.success) {
          show(latest.data);
          setNotice(
            "This window changed elsewhere; you are now seeing the current one.",
          );
          return;
        }
      }
      setFailure(
        error instanceof ApiError
          ? (error.problem.detail ?? error.message)
          : "The window could not be saved.",
      );
    },
  });

  function submit(event: SyntheticEvent) {
    event.preventDefault();
    setNotice(null);
    setFailure(null);
    if (fields.weekdays.length === 0) {
      setFailure("Pick at least one day.");
      return;
    }
    if (fields.start === "" || fields.end === "") {
      setFailure("Give a start and an end.");
      return;
    }
    if (fields.start === fields.end) {
      setFailure("The start and the end must differ.");
      return;
    }
    save.mutate({
      window: {
        weekdays: [...fields.weekdays].sort((a, b) => a - b),
        start_local: `${fields.start}:00`,
        end_local: `${fields.end}:00`,
      },
      version: current.version,
    });
  }

  function turnOff() {
    setNotice(null);
    setFailure(null);
    save.mutate({ window: null, version: current.version });
  }

  const toggleDay = (day: number, on: boolean) => {
    setFields((was) => ({
      ...was,
      weekdays: on
        ? [...was.weekdays.filter((d) => d !== day), day]
        : was.weekdays.filter((d) => d !== day),
    }));
  };

  const startId = `${baseId}-start`;
  const endId = `${baseId}-end`;
  const overnight = fields.start !== "" && fields.end < fields.start;
  return (
    <form noValidate onSubmit={submit} className="flex min-w-0 flex-col gap-3">
      <fieldset className="flex min-w-0 flex-col gap-2">
        <legend className="text-sm font-medium">Nights it runs</legend>
        <div className="flex flex-wrap gap-2">
          {DAYS.map(([short, name], day) => (
            <label
              key={name}
              className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-border bg-surface px-3 text-sm has-[:checked]:border-accent has-[:checked]:bg-accent-soft md:min-h-9"
            >
              <input
                type="checkbox"
                aria-label={name}
                checked={fields.weekdays.includes(day)}
                onChange={(event) => {
                  toggleDay(day, event.target.checked);
                }}
              />
              <span aria-hidden="true">{short}</span>
            </label>
          ))}
        </div>
      </fieldset>
      <div className="grid grid-cols-2 gap-2">
        <div className={FIELD_LABEL}>
          <label htmlFor={startId}>Starts at</label>
          <input
            id={startId}
            type="time"
            value={fields.start}
            onChange={(event) => {
              setFields((was) => ({ ...was, start: event.target.value }));
            }}
            className={FIELD}
          />
        </div>
        <div className={FIELD_LABEL}>
          <label htmlFor={endId}>Ends at</label>
          <input
            id={endId}
            type="time"
            value={fields.end}
            onChange={(event) => {
              setFields((was) => ({ ...was, end: event.target.value }));
            }}
            className={FIELD}
          />
        </div>
      </div>
      <p className={HINT}>
        Local times in the workspace timezone. An end before the start runs
        overnight and finishes the next morning
        {overnight ? "; this one does." : "."}
      </p>
      {failure !== null && (
        <p role="alert" className={ERROR_TEXT}>
          {failure}
        </p>
      )}
      <p role="status" className={`${HINT} empty:hidden`}>
        {notice}
      </p>
      <div className="flex flex-wrap gap-2">
        <button
          type="submit"
          className={BUTTON_PRIMARY}
          disabled={save.isPending}
        >
          Save
        </button>
        {current.version !== null && (
          <button
            type="button"
            className={BUTTON_SECONDARY}
            disabled={save.isPending}
            onClick={turnOff}
          >
            {offText}
          </button>
        )}
      </div>
    </form>
  );
}
