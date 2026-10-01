// Swap one plan item for another task (P1-11, J1): a dialog listing what the day can
// bring in instead (`GET /v1/plan/{day}/alternates`, read when it opens). Picking one
// swaps it in at the same place; Escape or Cancel closes it with nothing changed.
import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useRef } from "react";

import type { AlternateOut } from "../../api/types.gen";
import { LABEL_TEXT, type Label } from "../common/LabelChip";
import {
  BUTTON_SECONDARY,
  DIALOG_BACKDROP,
  DIALOG_PANEL,
  DIALOG_TITLE,
  HINT,
} from "../common/ui";
import { formatDay, formatMinutes } from "./format";
import { alternatesQuery } from "./queries";

function details(alternate: AlternateOut): string {
  const parts: string[] = [];
  const label = alternate.label as Label | null;
  if (label !== null) parts.push(LABEL_TEXT[label]);
  if (label !== "ai" && alternate.estimate_minutes !== null) {
    parts.push(formatMinutes(alternate.estimate_minutes));
  }
  if (alternate.due_on !== null)
    parts.push(`due ${formatDay(alternate.due_on)}`);
  return parts.join(" · ");
}

export function SwapPicker({
  day,
  title,
  onPick,
  onClose,
}: {
  day: string;
  /** The title of the item being swapped out. */
  title: string;
  onPick: (alternate: AlternateOut) => void;
  onClose: () => void;
}) {
  const titleId = useId();
  const alternates = useQuery(alternatesQuery(day));
  const cancel = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    cancel.current?.focus();
  }, []);
  const items = alternates.data ?? [];
  return (
    <div className={DIALOG_BACKDROP}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={DIALOG_PANEL}
        onKeyDown={(event) => {
          if (event.key === "Escape") onClose();
        }}
      >
        <h3 id={titleId} className={DIALOG_TITLE}>
          Swap
        </h3>
        <p className={HINT}>Instead of {title}:</p>
        {alternates.isPending ? null : alternates.isError ? (
          <p className={HINT}>The other tasks could not be loaded.</p>
        ) : items.length === 0 ? (
          <p className={HINT}>Nothing else to bring in today.</p>
        ) : (
          <ul
            role="listbox"
            aria-label="Tasks to swap in"
            className="flex flex-col gap-1"
          >
            {items.map((alternate) => (
              <li
                key={alternate.id}
                role="option"
                aria-selected="false"
                tabIndex={0}
                onClick={() => {
                  onPick(alternate);
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onPick(alternate);
                  }
                }}
                className="flex min-h-11 cursor-pointer flex-col justify-center rounded-lg border border-border px-3 py-2 hover:border-border-strong focus-visible:outline-2 focus-visible:outline-accent"
              >
                <span className="font-medium">{alternate.title}</span>
                <span className="text-xs text-muted">{details(alternate)}</span>
              </li>
            ))}
          </ul>
        )}
        <div className="flex justify-end">
          <button
            ref={cancel}
            type="button"
            className={BUTTON_SECONDARY}
            onClick={onClose}
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
