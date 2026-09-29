// Phone navigation (under 768 px): a bar along the bottom, within thumb reach.
import { Link } from "@tanstack/react-router";

import { NAV_ITEMS } from "./nav";

export function BottomBar() {
  return (
    <nav
      aria-label="Primary"
      className="fixed inset-x-0 bottom-0 z-40 flex border-t border-border bg-surface pb-[env(safe-area-inset-bottom)] md:hidden"
    >
      {NAV_ITEMS.map((item) => (
        <Link
          key={item.label}
          to={item.to}
          {...(item.params ? { params: item.params } : {})}
          className="flex min-h-12 flex-1 items-center justify-center text-xs text-muted"
          activeProps={{
            className: "font-semibold text-accent",
            "aria-current": "page",
          }}
          activeOptions={{ exact: item.to === "/" }}
        >
          {item.label}
        </Link>
      ))}
    </nav>
  );
}
