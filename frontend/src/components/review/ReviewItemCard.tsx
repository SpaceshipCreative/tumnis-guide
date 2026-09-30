// One review item (P1-13, UX 7, UX 11): the generic renderer. The kind's slot says what
// the item is about and how `edit` collects its payload; the buttons are the kind's own
// actions (R-04), 44 px tall so they work by thumb. The card is focusable: the queue's
// keyboard map acts on the focused card. It is a card like the dashboard's (DS-01,
// ADR-0012): the title and the kind's name in the header row, the summary and the actions
// below; the item the `item` search param names has an accent border.
import { useId, useState, type Ref } from "react";

import type { ReviewItemOut } from "../../api/types.gen";
import {
  badge,
  BUTTON_PRIMARY,
  BUTTON_SECONDARY,
  CARD,
  CARD_BODY,
  CARD_HEADER,
  CARD_TITLE,
  FIELD,
  FIELD_LABEL,
} from "../common/ui";
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
      <label htmlFor={inputId} className={`${FIELD_LABEL} w-full sm:w-64`}>
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
          className={FIELD}
          autoFocus // the form opens on a key press: typing goes straight in
        />
      </label>
      <button type="submit" className={BUTTON_PRIMARY} disabled={busy}>
        Save
      </button>
      <button type="button" className={BUTTON_SECONDARY} onClick={onCancel}>
        Cancel
      </button>
    </form>
  );
}

/** The snooze choices (1 hour, 3 hours, tomorrow) and Cancel. */
export function SnoozeChoices({
  busy,
  onPick,
  onCancel,
}: {
  busy: boolean;
  onPick: (key: SnoozeKey) => void;
  onCancel: () => void;
}) {
  return (
    <div
      role="group"
      aria-label="Snooze until"
      className="flex flex-wrap gap-2"
    >
      {SNOOZES.map((choice) => (
        <button
          key={choice.key}
          type="button"
          className={BUTTON_SECONDARY}
          disabled={busy}
          onClick={() => {
            onPick(choice.key);
          }}
        >
          {choice.text}
        </button>
      ))}
      <button type="button" className={BUTTON_SECONDARY} onClick={onCancel}>
        Cancel
      </button>
    </div>
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
      className={`${CARD} aria-[current=true]:border-accent`}
    >
      <header className={CARD_HEADER}>
        <h2 id={titleId} className={CARD_TITLE}>
          {item.target_title ?? slot.name}
        </h2>
        <span className={badge("neutral")}>{slot.name}</span>
      </header>
      <div className={CARD_BODY}>
        <p className="text-sm text-muted">{slot.summary(item)}</p>

        {mode === "idle" && (
          <div className="flex flex-wrap gap-2">
            {item.actions.map((action) => (
              <button
                key={action}
                type="button"
                className={
                  action === item.primary_action
                    ? BUTTON_PRIMARY
                    : BUTTON_SECONDARY
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
              <button
                type="button"
                className={BUTTON_SECONDARY}
                onClick={onOpen}
              >
                Open
              </button>
            )}
          </div>
        )}

        {mode === "snooze" && (
          <SnoozeChoices
            busy={busy}
            onPick={(key) => {
              onDecide({ action: "snooze", snooze: key });
            }}
            onCancel={() => {
              onMode("idle");
            }}
          />
        )}

        {editor?.type === "label" && (
          <div role="group" aria-label="Label" className="flex flex-wrap gap-2">
            {LABELS.map((label) => (
              <button
                key={label.value}
                type="button"
                className={BUTTON_SECONDARY}
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
              className={BUTTON_SECONDARY}
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
      </div>
    </article>
  );
}
