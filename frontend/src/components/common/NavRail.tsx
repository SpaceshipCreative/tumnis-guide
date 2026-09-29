// Laptop navigation (768 px and up): a rail on the left.
import { Link } from "@tanstack/react-router";

import { NAV_ITEMS } from "./nav";

export function NavRail() {
  return (
    <nav
      aria-label="Primary"
      className="sticky top-0 hidden h-dvh w-56 shrink-0 flex-col gap-1 border-r border-border bg-surface px-3 py-4 md:flex"
    >
      <p className="px-3 pb-4 text-lg font-semibold">Tumnis Guide</p>
      {NAV_ITEMS.map((item) => (
        <Link
          key={item.label}
          to={item.to}
          {...(item.params ? { params: item.params } : {})}
          className="rounded-md px-3 py-2 text-muted hover:bg-surface-muted"
          activeProps={{
            className: "bg-surface-muted font-medium text-text",
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
