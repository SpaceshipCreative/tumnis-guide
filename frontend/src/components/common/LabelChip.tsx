// The label chip (P1-07, FR-3.3, FR-4.1, FR-4.2, UX 2, UX 9): who does the task, Human, AI
// or Hybrid. Pending while Jev decides (`Labeling…`, aria-busy); suggested when Jev was not
// sure (dashed, `Suggested: Hybrid`, the label itself still pending, R-08); confirmed once
// set. The one-line reason is the chip's description and tooltip. One click opens a
// three-option menu; 1, 2 and 3 pick Human, AI and Hybrid in it. Picking sends one PATCH
// (the override: a recorded human decision) and shows at once; a 409 puts the server's
// record back. An AI label can be undone for the session.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSelector } from "@xstate/store-react";
import { type KeyboardEvent, useEffect, useId, useRef, useState } from "react";

import { invalidateTaskViews } from "../../lib/task-cache";
import {
  type TaskOut,
  taskQueryKey,
  taskQueryOptions,
  useUpdateTask,
} from "../../lib/optimistic";
import { remember, undo, undoStore } from "../../lib/undo";
import { uiStore } from "../../stores/uiStore";

export type Label = "human" | "ai" | "hybrid";
export type LabelSource = "user" | "jev" | "agent" | "fallback";
export type LabelState = "pending" | "suggested" | "confirmed";

export const LABELS: readonly Label[] = ["human", "ai", "hybrid"];
export const LABEL_TEXT: Record<Label, string> = {
  human: "Human",
  ai: "AI",
  hybrid: "Hybrid",
};
const AI_SOURCES: ReadonlySet<LabelSource | null> = new Set([
  "jev",
  "fallback",
]);
const UNDO_TEXT = "AI label";

/** confirmed when the label is set; suggested when only a suggestion exists (tasks.rules). */
export function labelState(
  label: Label | null,
  suggestion: Label | null,
): LabelState {
  if (label !== null) return "confirmed";
  return suggestion === null ? "pending" : "suggested";
}

function chipText(
  state: LabelState,
  label: Label | null,
  suggestion: Label | null,
) {
  if (state === "confirmed" && label) return LABEL_TEXT[label];
  if (state === "suggested" && suggestion) {
    return `Suggested: ${LABEL_TEXT[suggestion]}`;
  }
  return "Labeling…";
}

const CHIP =
  "inline-flex min-h-11 items-center rounded-full border px-2.5 text-xs font-medium md:min-h-7";
const CHIP_STATE: Record<LabelState, string> = {
  pending: "border-border text-muted",
  suggested: "border-dashed border-accent text-accent",
  confirmed: "border-border bg-surface-muted",
};

export interface LabelChipProps {
  label: Label | null;
  source: LabelSource | null;
  reason: string | null;
  suggestion: Label | null;
  onOverride: (label: Label) => void;
  /** Set while the AI's label can be undone this session (UX 9). */
  onUndo?: (() => void) | undefined;
  /** Whether the reason shows beside the chip (it is always its description). */
  showReason?: boolean;
}

/** The chip, its reason and its override menu; the caller owns the writes. */
export function LabelChip({
  label,
  source,
  reason,
  suggestion,
  onOverride,
  onUndo,
  showReason = true,
}: LabelChipProps) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const menuRef = useRef<HTMLUListElement>(null);
  const chipRef = useRef<HTMLButtonElement>(null);
  const reasonId = useId();
  const menuId = useId();
  const state = labelState(label, suggestion);
  const text = chipText(state, label, suggestion);
  const describedBy = reason ? reasonId : undefined;

  useEffect(() => {
    if (open) menuRef.current?.focus();
  }, [open]);

  const choose = (picked: Label) => {
    setOpen(false);
    chipRef.current?.focus();
    onOverride(picked);
  };

  const onMenuKey = (event: KeyboardEvent<HTMLUListElement>) => {
    const index = ["1", "2", "3"].indexOf(event.key);
    const byKey = index >= 0 ? LABELS[index] : undefined;
    if (byKey) {
      event.preventDefault();
      choose(byKey);
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : LABELS.length - 1;
      setActive((a) => (a + step) % LABELS.length);
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      const current = LABELS[active];
      if (current) choose(current);
    } else if (event.key === "Escape" || event.key === "Tab") {
      if (event.key === "Escape") event.preventDefault();
      setOpen(false);
      chipRef.current?.focus();
    }
  };

  return (
    <span className="relative inline-flex max-w-full min-w-0 items-center gap-2">
      <button
        ref={chipRef}
        type="button"
        data-testid="label-chip"
        data-state={state}
        data-source={source ?? undefined}
        aria-busy={state === "pending" ? true : undefined}
        aria-describedby={describedBy}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        title={reason ?? undefined}
        onClick={() => {
          setActive(Math.max(0, label ? LABELS.indexOf(label) : 0));
          setOpen((o) => !o);
        }}
        className={`${CHIP} ${CHIP_STATE[state]} shrink-0`}
      >
        {text}
      </button>
      {reason && (
        <span
          id={reasonId}
          className={
            showReason ? "min-w-0 truncate text-xs text-muted" : "sr-only"
          }
        >
          {reason}
        </span>
      )}
      {onUndo && (
        <button
          type="button"
          onClick={onUndo}
          className="min-h-11 shrink-0 rounded px-1 text-xs font-medium text-accent hover:underline md:min-h-7"
        >
          Undo AI label
        </button>
      )}
      {open && (
        <ul
          ref={menuRef}
          id={menuId}
          role="listbox"
          aria-label="Label"
          tabIndex={-1}
          aria-activedescendant={`${menuId}-${String(active)}`}
          onKeyDown={onMenuKey}
          onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget)) {
              setOpen(false);
            }
          }}
          className="absolute top-full left-0 z-30 mt-1 flex min-w-32 flex-col rounded-md border border-border bg-surface py-1 text-sm shadow-lg"
        >
          {LABELS.map((option, index) => (
            <li
              key={option}
              id={`${menuId}-${String(index)}`}
              role="option"
              aria-selected={option === label}
              onMouseDown={(event) => {
                event.preventDefault(); // keep the focus in the menu until the pick
              }}
              onClick={() => {
                choose(option);
              }}
              className={`flex min-h-11 cursor-pointer items-center justify-between gap-4 px-3 md:min-h-8 ${
                index === active ? "bg-surface-muted" : ""
              }`}
            >
              <span>{LABEL_TEXT[option]}</span>
              <kbd aria-hidden="true" className="text-xs text-muted">
                {index + 1}
              </kbd>
            </li>
          ))}
        </ul>
      )}
    </span>
  );
}

/**
 * A person's pick, shown at once (FR-4.2): one PATCH `{label, version}` with an
 * Idempotency-Key through `useUpdateTask`; the pick shows until the write settles, then
 * the cache (the answer, or the server's record after a 409) does.
 */
export function useLabelOverride(
  task: { id: string; version: number } | undefined,
) {
  const update = useUpdateTask();
  const [picked, setPicked] = useState<Label | null>(null);
  const { status } = update;
  useEffect(() => {
    if (status === "success" || status === "error") setPicked(null);
  }, [status]);
  return {
    picked,
    pick: (label: Label) => {
      if (!task) return;
      setPicked(label);
      update.mutate({ id: task.id, patch: { label }, version: task.version });
    },
  };
}

/** A task's label fields, as a task read or a list row carries them. */
export interface LabelledTask {
  id: string;
  version: number;
  label: Label | null;
  label_source: LabelSource | null;
  label_reason: string | null;
  label_suggestion: Label | null;
}

/** The chip of a task already in hand (A1.1's Just added row, the drawer): its reason
 * beside it, and a pick shown at once until the write settles. */
export function TaskLabel({
  task,
  showReason = true,
}: {
  task: LabelledTask;
  showReason?: boolean;
}) {
  const { picked, pick } = useLabelOverride(task);
  return (
    <LabelChip
      label={picked ?? task.label}
      source={picked ? "user" : task.label_source}
      reason={picked ? null : task.label_reason}
      suggestion={picked ? null : task.label_suggestion}
      onOverride={pick}
      showReason={showReason}
    />
  );
}

/** The chip of one task, read from `GET /v1/tasks/{id}` and refreshed by /ws (R-05). */
export function TaskLabelChip({
  taskId,
  showReason = true,
}: {
  taskId: string;
  showReason?: boolean;
}) {
  const client = useQueryClient();
  const { data: task } = useQuery(taskQueryOptions(taskId));
  const { picked, pick } = useLabelOverride(task);
  const entries = useSelector(undoStore, (s) => s.context.entries);
  const aiChange =
    task && AI_SOURCES.has(task.label_source) ? task.change_id : null;

  // The AI's label write, undoable for the session (UX 9, R-09).
  useEffect(() => {
    if (!task || !aiChange) return;
    const known = undoStore
      .getSnapshot()
      .context.entries.some((e) => e.changeId === aiChange);
    if (!known) remember(task, UNDO_TEXT);
  }, [task, aiChange]);

  const entry = entries.find(
    (e) => e.taskId === taskId && e.changeId === aiChange,
  );
  const onUndo = entry
    ? () => {
        undo(entry)
          .then((restored: TaskOut) => {
            client.setQueryData(taskQueryKey(taskId), restored);
          })
          .catch(() => {
            uiStore.trigger.showNotice({ text: "Could not undo the label." });
          })
          .finally(() => {
            void invalidateTaskViews(client);
          });
      }
    : undefined;

  const label = picked ?? task?.label ?? null;
  return (
    <LabelChip
      label={label}
      source={picked ? "user" : (task?.label_source ?? null)}
      reason={picked ? null : (task?.label_reason ?? null)}
      suggestion={picked ? null : (task?.label_suggestion ?? null)}
      onOverride={pick}
      onUndo={picked ? undefined : onUndo}
      showReason={showReason}
    />
  );
}
