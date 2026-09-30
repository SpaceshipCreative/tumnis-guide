// Laptop navigation (768 px and up; DS-01): a sidebar on the left with the grouped links.
// The button at its foot collapses it to icons and back; the choice is remembered on this
// device (uiStore `sidebarCollapsed`).
import { useSelector } from "@xstate/store-react";

import { uiStore } from "../../stores/uiStore";
import { BrandMark, Icon } from "./icons";
import { NavGroups } from "./NavGroups";

export function NavRail() {
  const collapsed = useSelector(uiStore, (s) => s.context.sidebarCollapsed);
  return (
    <nav
      aria-label="Primary"
      data-collapsed={collapsed ? "" : undefined}
      className={`sticky top-0 hidden h-dvh shrink-0 flex-col border-r border-border bg-surface py-4 transition-[width] duration-200 md:flex ${collapsed ? "w-20 px-3" : "w-64 px-4"}`}
    >
      <div
        className={`mb-6 flex h-8 items-center gap-3 ${collapsed ? "justify-center" : "px-2"}`}
      >
        <BrandMark />
        <span className={collapsed ? "sr-only" : "text-base font-semibold"}>
          Tumnis Guide
        </span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <NavGroups collapsed={collapsed} />
      </div>
      <div
        className={`mt-4 flex border-t border-border pt-3 ${collapsed ? "justify-center" : "justify-end"}`}
      >
        <button
          type="button"
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          onClick={() => {
            uiStore.trigger.setSidebarCollapsed({ collapsed: !collapsed });
          }}
          className="inline-flex size-9 items-center justify-center rounded-lg text-muted hover:bg-surface-muted hover:text-text"
        >
          <Icon name={collapsed ? "expand" : "collapse"} />
        </button>
      </div>
    </nav>
  );
}
