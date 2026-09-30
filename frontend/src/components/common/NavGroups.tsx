// The grouped navigation links (DS-01) the laptop sidebar and the phone drawer share. The
// active link is tinted violet (the router marks it `data-status="active"` and
// `aria-current="page"`). Collapsed, only the icons show; each link keeps its name.
import { Link } from "@tanstack/react-router";
import { useId } from "react";

import { Icon } from "./icons";
import { NAV_GROUPS, type NavItem } from "./nav";

function NavLink({
  item,
  collapsed,
  onNavigate,
}: {
  item: NavItem;
  collapsed: boolean;
  onNavigate?: (() => void) | undefined;
}) {
  return (
    <Link
      to={item.to}
      {...(item.params ? { params: item.params } : {})}
      activeOptions={{ exact: item.to === "/" }}
      title={collapsed ? item.label : undefined}
      onClick={onNavigate}
      className={`group flex min-h-11 items-center gap-3 rounded-lg px-3 text-sm font-medium text-muted transition-colors hover:bg-surface-muted hover:text-text data-[status=active]:bg-accent-soft data-[status=active]:text-text md:min-h-10 ${collapsed ? "justify-center" : ""}`}
    >
      <Icon
        name={item.icon}
        className="size-5 group-data-[status=active]:text-accent"
      />
      <span className={collapsed ? "sr-only" : "truncate"}>{item.label}</span>
    </Link>
  );
}

function Group({
  label,
  items,
  collapsed,
  onNavigate,
}: {
  label: string;
  items: readonly NavItem[];
  collapsed: boolean;
  onNavigate?: (() => void) | undefined;
}) {
  const id = useId();
  return (
    <div>
      <p
        id={id}
        className={
          collapsed
            ? "sr-only"
            : "px-3 pb-2 text-xs font-semibold tracking-wide text-muted uppercase"
        }
      >
        {label}
      </p>
      {collapsed && (
        <div aria-hidden="true" className="mx-auto mb-2 h-px w-6 bg-border" />
      )}
      <ul aria-labelledby={id} className="flex flex-col gap-1">
        {items.map((item) => (
          <li key={item.label}>
            <NavLink
              item={item}
              collapsed={collapsed}
              onNavigate={onNavigate}
            />
          </li>
        ))}
      </ul>
    </div>
  );
}

export function NavGroups({
  collapsed = false,
  onNavigate,
}: {
  collapsed?: boolean;
  onNavigate?: (() => void) | undefined;
}) {
  return (
    <div className="flex flex-col gap-6">
      {NAV_GROUPS.map((group) => (
        <Group
          key={group.label}
          label={group.label}
          items={group.items}
          collapsed={collapsed}
          onNavigate={onNavigate}
        />
      ))}
    </div>
  );
}
