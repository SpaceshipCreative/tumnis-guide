// The phone's navigation drawer (DS-01, UX 11): the header's menu button slides it in from
// the left with the grouped links. It is a modal dialog: the page behind is inert (the
// shell sets `inert`), Tab and Shift+Tab wrap inside it, and Escape, the backdrop, the close
// button or a link close it and give focus back to the menu button. On a laptop the
// sidebar shows instead, so a drawer left open closes.
import { useSelector } from "@xstate/store-react";
import { useEffect, useRef } from "react";

import { dialogKeyDown } from "../../lib/focusTrap";
import { useIsLaptop } from "../../lib/media";
import { uiStore } from "../../stores/uiStore";
import { MENU_BUTTON_ID } from "./AppHeader";
import { BrandMark, Icon } from "./icons";
import { NavGroups } from "./NavGroups";

function close(): void {
  uiStore.trigger.setNavOpen({ open: false });
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
          dialogKeyDown(event, close);
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
