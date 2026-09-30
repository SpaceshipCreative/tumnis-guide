// Phone navigation (under 768 px): a bar along the bottom, within thumb reach, each
// destination an icon over its name (DS-01). The drawer behind the header's menu button
// holds the same links in their groups.
import { Link } from "@tanstack/react-router";

import { Icon } from "./icons";
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
          className="flex min-h-14 flex-1 flex-col items-center justify-center gap-0.5 text-[0.6875rem] font-medium text-muted data-[status=active]:text-accent"
          activeOptions={{ exact: item.to === "/" }}
        >
          <Icon name={item.icon} />
          {item.label}
        </Link>
      ))}
    </nav>
  );
}
