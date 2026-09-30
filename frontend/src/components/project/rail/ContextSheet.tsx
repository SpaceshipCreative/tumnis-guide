// The Context sheet (P0-24, FR-2.8): on the phone, a "Context" button opens one bottom
// sheet holding the rail's sections. Escape or Close shuts it and gives focus back.
import { useSelector } from "@xstate/store-react";
import { useEffect, useId, useRef } from "react";

import { uiStore } from "../../../stores/uiStore";
import type { Project } from "../types";
import { RailSections } from "./RailSections";

export function ContextSheet({
  project,
  onOpenTask,
}: {
  project: Project;
  onOpenTask: (taskId: string) => void;
}) {
  const open = useSelector(uiStore, (s) => s.context.contextSheetOpen);
  const trigger = useRef<HTMLButtonElement>(null);
  const sheet = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const close = () => {
    uiStore.trigger.setContextSheet({ open: false });
    trigger.current?.focus();
  };

  useEffect(() => {
    if (open) sheet.current?.focus();
  }, [open]);
  // Leaving the page closes the sheet.
  useEffect(
    () => () => {
      uiStore.trigger.setContextSheet({ open: false });
    },
    [],
  );

  return (
    <>
      <button
        ref={trigger}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => {
          uiStore.trigger.setContextSheet({ open: true });
        }}
        className="min-h-11 self-start rounded-md border border-border px-3 text-sm font-medium"
      >
        Context
      </button>
      {open && (
        <div className="fixed inset-0 z-40 flex flex-col justify-end bg-black/30">
          <div
            ref={sheet}
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            tabIndex={-1}
            onKeyDown={(event) => {
              if (event.key === "Escape") close();
            }}
            className="flex max-h-[85dvh] flex-col gap-2 overflow-y-auto rounded-t-xl border-t border-border bg-surface px-4 pt-3 pb-6 outline-none"
          >
            <div className="flex items-center justify-between">
              <h2 id={titleId} className="text-base font-semibold">
                Context
              </h2>
              <button
                type="button"
                onClick={close}
                className="min-h-11 rounded-md px-3 text-sm font-medium text-accent"
              >
                Close
              </button>
            </div>
            <RailSections
              project={project}
              onOpenTask={(taskId) => {
                uiStore.trigger.setContextSheet({ open: false });
                onOpenTask(taskId);
              }}
            />
          </div>
        </div>
      )}
    </>
  );
}
