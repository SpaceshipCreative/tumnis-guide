// A plain-words notice (P0-24): why a board move was refused, or why an undo could not
// run. Dismissed by the reader, or after a while.
import { useSelector } from "@xstate/store-react";
import { useEffect } from "react";

import { uiStore } from "../../stores/uiStore";

const SHOW_MS = 8_000; // (plan default)

export function NoticeToast() {
  const notice = useSelector(uiStore, (s) => s.context.notice);
  useEffect(() => {
    if (notice === null) return;
    const timer = window.setTimeout(() => {
      uiStore.trigger.clearNotice();
    }, SHOW_MS);
    return () => {
      window.clearTimeout(timer);
    };
  }, [notice]);
  if (notice === null) return null;
  return (
    <div className="pointer-events-none fixed inset-x-0 top-4 z-50 flex justify-center px-4">
      <div
        role="alert"
        className="pointer-events-auto flex max-w-md items-center gap-3 rounded-md border border-border bg-surface px-4 py-3 text-sm shadow-lg"
      >
        <p>{notice}</p>
        <button
          type="button"
          className="min-h-11 rounded px-2 font-medium text-accent md:min-h-8"
          onClick={() => {
            uiStore.trigger.clearNotice();
          }}
        >
          Dismiss
        </button>
      </div>
    </div>
  );
}
