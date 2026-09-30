// The session's Undo (P0-24, UX 9): the latest task action with an Undo button, and Mod+Z
// (Ctrl or Cmd + Z) anywhere outside a text field. After an undo every task view refetches
// and shows the restored task. An entry being undone is skipped, so a second Mod+Z (or
// Undo click) while the first is on its way undoes the one before it instead of sending the
// same change twice.
import { useQueryClient } from "@tanstack/react-query";
import { useSelector } from "@xstate/store-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, ConflictError } from "../../lib/fetch";
import { invalidateTaskViews } from "../../lib/task-cache";
import { undo, undoStore, type UndoEntry } from "../../lib/undo";
import { uiStore } from "../../stores/uiStore";

const SHOW_MS = 10_000; // (plan default) the toast hides; Mod+Z still undoes

/** A field that takes typing, where Mod+Z belongs to the field. */
function isEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement
  );
}

function refusal(error: unknown): string {
  if (
    error instanceof ConflictError &&
    error.problem.code === "already_undone"
  ) {
    return "That change was already undone.";
  }
  if (error instanceof ConflictError) {
    return "The task changed since, so that action can no longer be undone.";
  }
  if (error instanceof ApiError && error.status === 404) {
    return "That task no longer exists, so the action can't be undone.";
  }
  return "Could not undo. Try again.";
}

/** The latest entry not being undone right now. */
function latestIdle(
  entries: readonly UndoEntry[],
  busy: ReadonlySet<string>,
): UndoEntry | undefined {
  return entries.findLast((e) => !busy.has(e.changeId));
}

export function UndoToast() {
  const entries = useSelector(undoStore, (s) => s.context.entries);
  const queryClient = useQueryClient();
  const [hidden, setHidden] = useState<string | null>(null);
  // The change ids being undone: the ref for the key handler (two presses can come before
  // a render), the state to redraw the toast.
  const inFlight = useRef(new Set<string>());
  const [busy, setBusy] = useState<ReadonlySet<string>>(() => new Set());
  const latest = latestIdle(entries, busy);

  const run = useCallback(
    async (entry: UndoEntry) => {
      if (inFlight.current.has(entry.changeId)) return;
      inFlight.current.add(entry.changeId);
      setBusy(new Set(inFlight.current));
      try {
        await undo(entry);
      } catch (error) {
        uiStore.trigger.showNotice({ text: refusal(error) });
      } finally {
        inFlight.current.delete(entry.changeId);
        setBusy(new Set(inFlight.current));
        await invalidateTaskViews(queryClient);
      }
    },
    [queryClient],
  );

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const mod = event.ctrlKey || event.metaKey;
      if (!mod || event.shiftKey || event.altKey) return;
      if (event.key.toLowerCase() !== "z" || isEditable(event.target)) return;
      const entry = latestIdle(
        undoStore.getSnapshot().context.entries,
        inFlight.current,
      );
      if (!entry) return;
      event.preventDefault();
      void run(entry);
    }
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [run]);

  const changeId = latest?.changeId;
  useEffect(() => {
    if (changeId === undefined) return;
    const timer = window.setTimeout(() => {
      setHidden(changeId);
    }, SHOW_MS);
    return () => {
      window.clearTimeout(timer);
    };
  }, [changeId]);

  if (!latest || hidden === latest.changeId) return null;
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-36 z-50 flex justify-center px-4 md:bottom-20">
      <div
        role="status"
        aria-label="Undo"
        className="pointer-events-auto flex max-w-md items-center gap-3 rounded-md border border-border bg-surface px-4 py-3 text-sm shadow-lg"
      >
        <p>{latest.label}</p>
        <button
          type="button"
          title="Undo (Ctrl+Z)"
          className="min-h-11 rounded px-2 font-medium text-accent md:min-h-8"
          onClick={() => {
            void run(latest);
          }}
        >
          Undo
        </button>
        <button
          type="button"
          aria-label="Dismiss"
          className="min-h-11 rounded px-2 text-muted md:min-h-8"
          onClick={() => {
            setHidden(latest.changeId);
          }}
        >
          ×
        </button>
      </div>
    </div>
  );
}
