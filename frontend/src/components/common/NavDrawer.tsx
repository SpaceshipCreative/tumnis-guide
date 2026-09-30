// The phone's navigation drawer (DS-01, UX 11): the header's menu button slides it in from
// the left with the grouped links. It is a modal dialog: the page behind is inert (the
// shell sets `inert`), Tab and Shift+Tab wrap inside it, and Escape, the backdrop, the close
// button or a link close it and give focus back to the menu button. On a laptop the
// sidebar shows instead, so a drawer left open closes.
import { useSelector } from "@xstate/store-react";
import { useEffect, useRef, type KeyboardEvent } from "react";

import { useIsLaptop } from "../../lib/media";
import { uiStore } from "../../stores/uiStore";
import { MENU_BUTTON_ID } from "./AppHeader";
import { BrandMark, Icon } from "./icons";
import { NavGroups } from "./NavGroups";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])';

function close(): void {
  uiStore.trigger.setNavOpen({ open: false });
}

/** Keeps Tab and Shift+Tab inside `root`, wrapping at either end. */
function trapTab(event: KeyboardEvent<HTMLElement>): void {
  const root = event.currentTarget;
  const items = Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE));
  const first = items[0];
  const last = items.at(-1);
  if (!first || !last) {
    event.preventDefault();
    return;
  }
  const active = document.activeElement;
  const inside = active instanceof Node && root.contains(active);
  if (event.shiftKey && (!inside || active === first)) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && (!inside || active === last)) {
    event.preventDefault();
    first.focus();
  }
}

function Panel() {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
    return () => {
      document.getElementById(MENU_BUTTON_ID)?.focus();
    };
  }, []);

  return (
    <div className="fixed inset-0 z-50 md:hidden">
      <div
        aria-hidden="true"
        className="absolute inset-0 bg-gray-900/40"
        onClick={close}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Menu"
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            close();
          } else if (event.key === "Tab") {
            trapTab(event);
          }
        }}
        className="absolute inset-y-0 left-0 flex w-72 animate-slide-in max-w-[85vw] flex-col overflow-y-auto bg-surface px-4 pt-3 pb-[max(1rem,env(safe-area-inset-bottom))] shadow-xl"
      >
        <div className="mb-6 flex items-center gap-3 pl-2">
          <BrandMark />
          <span className="flex-1 text-base font-semibold">Tumnis Guide</span>
          <button
            ref={closeRef}
            type="button"
            aria-label="Close menu"
            onClick={close}
            className="inline-flex size-11 items-center justify-center rounded-full text-muted hover:bg-surface-muted hover:text-text"
          >
            <Icon name="close" />
          </button>
        </div>
        <nav aria-label="Menu">
          <NavGroups onNavigate={close} />
        </nav>
      </div>
    </div>
  );
}

export function NavDrawer() {
  const open = useSelector(uiStore, (s) => s.context.navOpen);
  const laptop = useIsLaptop();
  useEffect(() => {
    if (laptop && open) close();
  }, [laptop, open]);
  return open && !laptop ? <Panel /> : null;
}
