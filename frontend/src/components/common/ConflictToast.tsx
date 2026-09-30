// Says so when a write lost to a newer version (REL-2): the screen now shows the server's
// record. A polite live region, dismissed by the reader or the next edit.
import { useSelector } from "@xstate/store-react";

import { uiStore } from "../../stores/uiStore";

const ENTITY_NAMES: Record<string, string> = {
  task: "task",
  project: "project",
  settings: "settings",
  brief: "brief",
};

export function ConflictToast() {
  const conflict = useSelector(uiStore, (s) => s.context.conflict);
  return (
    <div
      role="status"
      aria-live="polite"
      className="pointer-events-none fixed inset-x-0 bottom-20 z-50 flex justify-center px-4 md:bottom-6"
    >
      {conflict && (
        <div className="pointer-events-auto flex max-w-md items-center gap-3 rounded-md border border-border bg-surface px-4 py-3 text-sm shadow-lg">
          <p>
            This {ENTITY_NAMES[conflict.entity] ?? "item"} was changed
            elsewhere; you are now seeing the latest version.
          </p>
          <button
            type="button"
            className="rounded px-2 py-1 font-medium text-accent"
            onClick={() => {
              uiStore.trigger.clearConflict();
            }}
          >
            Dismiss
          </button>
        </div>
      )}
    </div>
  );
}
