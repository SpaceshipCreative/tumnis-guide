// One review item (P1-13, UX 7, UX 11): the generic renderer. The kind's slot says what
// the item is about and how `edit` collects its payload; the buttons are the kind's own
// actions (R-04), 44 px tall so they work by thumb. The card is focusable: the queue's
// keyboard map acts on the focused card.
import { useId, useState, type Ref } from "react";

import type { ReviewItemOut } from "../../api/types.gen";
import { ANSWER, LABELS, slotFor, type Editor } from "./slots";

export type Mode = "idle" | "edit" | "answer" | "snooze";
export type Action = DecideAction["action"];
export type SnoozeKey = "1" | "3" | "t";

export interface DecideAction {
  action:
    "accept" | "edit" | "reject" | "snooze" | "answer" | "approve" | "deny";
  payload?: Record<string, unknown>;
  snooze?: SnoozeKey;
}

const BUTTON =
  "min-h-11 rounded-md border border-border bg-surface px-3 text-sm font-medium hover:bg-surface-muted disabled:opacity-50";
const PRIMARY_BUTTON =
  "min-h-11 rounded-md bg-accent px-3 text-sm font-medium text-accent-contrast disabled:opacity-50";

export const SNOOZES: readonly { key: SnoozeKey; text: string }[] = [
  { key: "1", text: "1 hour" },
  { key: "3", text: "3 hours" },
  { key: "t", text: "Tomorrow" },
];

function actionText(action: string): string {
  return action.charAt(0).toUpperCase() + action.slice(1);
}

/** Whether `o` (and the Open button) can open this item's target. */
export function canOpen(item: ReviewItemOut): boolean {
  return (
    (item.target_type === "task" && item.project_id !== null) ||
    item.target_type === "project"
  );
}

export function editorFor(item: ReviewItemOut, mode: Mode): Editor | undefined {
  if (mode === "answer") return ANSWER;
  if (mode === "edit") return slotFor(item.kind).edit;
  return undefined;
}

function ValueForm({
  editor,
  onSubmit,
  onCancel,
  busy,
}: {
  editor: Extract<Editor, { type: "number" | "text" }>;
  onSubmit: (payload: Record<string, unknown>) => void;
  onCancel: () => void;
  busy: boolean;
}) {
  const inputId = useId();
  const [value, setValue] = useState("");
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (value.trim() === "") return;
        if (editor.type === "number") {
          const n = Number(value);
          if (!Number.isSafeInteger(n) || n < 1) return;
          onSubmit({ [editor.field]: n });
          return;
        }
        onSubmit({ [editor.field]: value.trim() });
      }}
      className="flex flex-wrap items-end gap-2"
    >
      <label htmlFor={inputId} className="flex flex-col gap-1 text-sm">
        {editor.label}
        <input
          id={inputId}
          type={editor.type === "number" ? "number" : "text"}
          min={editor.type === "number" ? 1 : undefined}
          value={value}
          onChange={(event) => {
            setValue(event.target.value);
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape") onCancel();
          }}
          className="min-h-11 rounded-md border border-border bg-surface px-2"
          autoFocus // the form opens on a key press: typing goes straight in
        />
      </label>
      <button type="submit" className={PRIMARY_BUTTON} disabled={busy}>
        Save
      </button>
      <button type="button" className={BUTTON} onClick={onCancel}>
        Cancel
      </button>
    </form>
  );
}

export interface ReviewItemCardProps {
  item: ReviewItemOut;
  current: boolean;
  mode: Mode;
  busy: boolean;
  articleRef: Ref<HTMLElement>;
  onFocus: () => void;
  onMode: (mode: Mode) => void;
  onDecide: (decision: DecideAction) => void;
  onOpen: () => void;
}

export function ReviewItemCard({
  item,
  current,
  mode,
  busy,
  articleRef,
  onFocus,
  onMode,
  onDecide,
  onOpen,
}: ReviewItemCardProps) {
  const titleId = useId();
  const slot = slotFor(item.kind);
  const editor = editorFor(item, mode);

  const press = (action: Action) => {
    if (action === "edit" || action === "answer" || action === "snooze") {
      onMode(action);
      return;
    }
    onDecide({ action });
  };

  return (
    <article
      ref={articleRef}
      tabIndex={0}
      aria-labelledby={titleId}
      aria-current={current ? "true" : undefined}
      onFocus={(event) => {
        if (event.target === event.currentTarget) onFocus();
      }}
      className="flex flex-col gap-3 rounded-lg border border-border bg-surface p-4 outline-none focus-visible:ring-2 focus-visible:ring-accent aria-[current=true]:border-accent"
    >
      <header className="flex flex-col gap-1">
        <p className="text-xs font-medium uppercase tracking-wide text-muted">
          {slot.name}
        </p>
        <h2 id={titleId} className="text-base font-semibold">
          {item.target_title ?? slot.name}
        </h2>
        <p className="text-sm text-muted">{slot.summary(item)}</p>
      </header>

      {mode === "idle" && (
        <div className="flex flex-wrap gap-2">
          {item.actions.map((action) => (
            <button
              key={action}
              type="button"
              className={
                action === item.primary_action ? PRIMARY_BUTTON : BUTTON
              }
              disabled={busy}
              onClick={() => {
                press(action as Action);
              }}
            >
              {actionText(action)}
            </button>
          ))}
          {canOpen(item) && (
            <button type="button" className={BUTTON} onClick={onOpen}>
              Open
            </button>
          )}
        </div>
      )}

      {mode === "snooze" && (
        <div
          role="group"
          aria-label="Snooze until"
          className="flex flex-wrap gap-2"
        >
          {SNOOZES.map((choice) => (
            <button
              key={choice.key}
              type="button"
              className={BUTTON}
              disabled={busy}
              onClick={() => {
                onDecide({ action: "snooze", snooze: choice.key });
              }}
            >
              {choice.text}
            </button>
          ))}
          <button
            type="button"
            className={BUTTON}
            onClick={() => {
              onMode("idle");
            }}
          >
            Cancel
          </button>
        </div>
      )}

      {editor?.type === "label" && (
        <div role="group" aria-label="Label" className="flex flex-wrap gap-2">
          {LABELS.map((label) => (
            <button
              key={label.value}
              type="button"
              className={BUTTON}
              disabled={busy}
              onClick={() => {
                onDecide({ action: "edit", payload: { label: label.value } });
              }}
            >
              {label.text}
            </button>
          ))}
          <button
            type="button"
            className={BUTTON}
            onClick={() => {
              onMode("idle");
            }}
          >
            Cancel
          </button>
        </div>
      )}

      {editor !== undefined && editor.type !== "label" && (
        <ValueForm
          editor={editor}
          busy={busy}
          onCancel={() => {
            onMode("idle");
          }}
          onSubmit={(payload) => {
            onDecide({
              action: mode === "answer" ? "answer" : "edit",
              payload,
            });
          }}
        />
      )}
    </article>
  );
}
