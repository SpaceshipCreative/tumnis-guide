// Asks before a destructive action that cannot be undone (P0-26): revoking a key,
// discarding a dead letter, signing devices out. Escape or Cancel closes it; the confirm
// button names the action.
import { useEffect, useId, useRef, type ReactNode } from "react";

import { DANGER, SECONDARY } from "./styles";
import { DIALOG_BACKDROP, DIALOG_PANEL, DIALOG_TITLE } from "../common/ui";

export function ConfirmDialog({
  title,
  children,
  confirmLabel,
  busy = false,
  onConfirm,
  onCancel,
}: {
  title: string;
  children?: ReactNode;
  confirmLabel: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const titleId = useId();
  const cancel = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    cancel.current?.focus();
  }, []);
  return (
    <div className={DIALOG_BACKDROP}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={DIALOG_PANEL}
        onKeyDown={(event) => {
          if (event.key === "Escape") onCancel();
        }}
      >
        <h3 id={titleId} className={DIALOG_TITLE}>
          {title}
        </h3>
        {children}
        <div className="flex flex-wrap justify-end gap-2">
          <button
            ref={cancel}
            type="button"
            className={SECONDARY}
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            type="button"
            className={DANGER}
            disabled={busy}
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
