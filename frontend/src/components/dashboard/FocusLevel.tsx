// The dashboard's focus level chip (P2-15, FR-10.1, FR-10.9): the level in force today,
// the workspace's level (one tap to change it, `PUT /v1/focus/level`) and "Less today",
// which lowers today's level one step until the day closes (`POST /v1/focus/less`). Both
// answer the new `GET /v1/focus/current`, which goes straight into the cache, so the
// focus bar follows at once.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useId } from "react";

import {
  focusGetCurrentOptions,
  focusGetCurrentQueryKey,
} from "../../api/@tanstack/react-query.gen";
import type { FocusCurrentOut } from "../../api/types.gen";
import { zFocusCurrentOut } from "../../api/zod.gen";
import { apiWrite, useWrite } from "../../lib/fetch";
import { BUTTON_SECONDARY } from "../common/ui";

type Level = FocusCurrentOut["level"];

export const FOCUS_LEVELS: readonly { level: Level; text: string }[] = [
  { level: "quiet", text: "Quiet" },
  { level: "nudge", text: "Nudge" },
  { level: "coach", text: "Coach" },
  { level: "guardrail", text: "Guardrail" },
];

const TEXT = Object.fromEntries(
  FOCUS_LEVELS.map((l) => [l.level, l.text]),
) as Record<Level, string>;

export function FocusLevel() {
  const queryClient = useQueryClient();
  const current = useQuery(focusGetCurrentOptions());
  const selectId = useId();
  const show = (answer: FocusCurrentOut) => {
    queryClient.setQueryData(focusGetCurrentQueryKey(), answer);
  };
  const setLevel = useWrite<
    { level: Level; idempotencyKey?: string },
    FocusCurrentOut
  >({
    mutationFn: ({ idempotencyKey, ...body }) =>
      apiWrite({
        kind: "create",
        method: "PUT",
        path: "/focus/level",
        body,
        idempotencyKey,
        schema: zFocusCurrentOut,
      }),
    onSuccess: show,
  });
  const less = useWrite<{ idempotencyKey?: string }, FocusCurrentOut>({
    mutationFn: ({ idempotencyKey }) =>
      apiWrite({
        kind: "create",
        method: "POST",
        path: "/focus/less",
        body: {},
        idempotencyKey,
        schema: zFocusCurrentOut,
      }),
    onSuccess: show,
  });

  const data = current.data;
  if (!data) return null;
  const busy = setLevel.isPending || less.isPending;
  return (
    <div
      className="flex flex-wrap items-center gap-2"
      role="group"
      aria-label="Focus level"
    >
      <span className="text-sm text-muted">
        {data.override_level === null
          ? `Focus: ${TEXT[data.level]}`
          : `Focus today: ${TEXT[data.level]} (usually ${TEXT[data.workspace_level]})`}
      </span>
      <label htmlFor={selectId} className="sr-only">
        Workspace focus level
      </label>
      <select
        id={selectId}
        value={data.workspace_level}
        disabled={busy}
        onChange={(e) => {
          setLevel.mutate({ level: e.target.value as Level });
        }}
        className="min-h-11 rounded-lg border border-border bg-surface px-2 text-base md:min-h-8 md:text-sm"
      >
        {FOCUS_LEVELS.map((l) => (
          <option key={l.level} value={l.level}>
            {l.text}
          </option>
        ))}
      </select>
      {(setLevel.isError || less.isError) && (
        <span role="alert" className="text-sm text-danger">
          The focus level was not changed. Try again.
        </span>
      )}
      {data.level !== "quiet" && (
        <button
          type="button"
          className={`${BUTTON_SECONDARY} md:min-h-8 md:py-1`}
          disabled={busy}
          onClick={() => {
            less.mutate({});
          }}
        >
          Less today
        </button>
      )}
    </div>
  );
}
