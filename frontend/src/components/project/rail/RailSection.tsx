// A Context rail section (P0-24, FR-2.7): a button showing the section's name and a
// one-line summary; it opens the section in place.
import { useId, type ReactNode } from "react";

import { BUTTON_DANGER, BUTTON_SECONDARY, FIELD } from "../../common/ui";

export function RailSection({
  title,
  summary,
  open,
  onToggle,
  children,
}: {
  title: string;
  summary: string;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  const panelId = useId();
  return (
    <div className="border-b border-border last:border-b-0">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={onToggle}
        className="flex min-h-11 w-full flex-col items-start gap-0.5 py-2 text-left"
      >
        <span className="text-sm font-semibold">{title}</span>
        <span className="line-clamp-1 text-sm text-muted">{summary}</span>
      </button>
      {open && (
        <div id={panelId} className="flex flex-col gap-2 pb-3">
          {children}
        </div>
      )}
    </div>
  );
}

// The rail's fields and Save buttons share the app's primitives (DS-01, common/ui.ts);
// Save stays compact on a laptop, where the rail is a narrow column.
export const fieldClass = FIELD;
export const saveClass = `${BUTTON_SECONDARY} self-start px-3 md:min-h-8`;
export const deleteClass = `${BUTTON_DANGER} self-start px-3 md:min-h-8`;
