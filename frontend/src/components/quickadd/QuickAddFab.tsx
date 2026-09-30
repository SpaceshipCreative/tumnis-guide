// Quick add on the phone (P0-23, UX 11): a round button in the bottom third, right side
// for the right thumb, above the bottom bar and the safe area. It opens the quick-add
// dialog through the ui store; on a laptop `/` does. The shell's quick-add host shows it
// on every route (P0-25).
import { uiStore } from "../../stores/uiStore";

export function QuickAddFab() {
  return (
    <button
      type="button"
      aria-label="Quick add"
      aria-keyshortcuts="/"
      onClick={() => {
        uiStore.trigger.openQuickAdd();
      }}
      className="fixed right-4 bottom-[calc(4.5rem+env(safe-area-inset-bottom))] z-40 flex size-14 items-center justify-center rounded-full bg-accent text-accent-contrast shadow-lg md:hidden"
    >
      <svg
        aria-hidden="true"
        viewBox="0 0 24 24"
        className="size-7"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
      >
        <path d="M12 5v14M5 12h14" />
      </svg>
    </button>
  );
}
