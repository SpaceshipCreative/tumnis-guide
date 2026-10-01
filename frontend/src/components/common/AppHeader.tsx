// The shell's header (DS-01, UX 6, UX 7, UX 11): on the phone the menu button for the
// navigation drawer; then search (the Mod+K palette, P0-25), the review queue with its
// count (the one badge, quiet at zero), help with the keyboard shortcuts, and the account
// menu (settings, the colour theme, close the day, sign out). Every control is 44 px on the phone.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { useSelector } from "@xstate/store-react";
import { useEffect, useId, useRef, useState } from "react";

import { ApiError, apiWrite } from "../../lib/fetch";
import type { ThemeChoice } from "../../lib/theme";
import { uiStore } from "../../stores/uiStore";
import { reviewCountQuery } from "../dashboard/queries";
import { accountQuery } from "../settings/queries";
import { Icon } from "./icons";
import { MenuButton } from "./Menu";

export const MENU_BUTTON_ID = "shell-menu-button";

const ROUND =
  "inline-flex size-11 items-center justify-center rounded-full text-muted hover:bg-surface-muted hover:text-text md:size-9";

function isMac(): boolean {
  return /Mac|iPhone|iPad/.test(navigator.userAgent);
}

/** How this device writes Mod+K. */
function modK(): string {
  return isMac() ? "⌘K" : "Ctrl K";
}

function SearchButton() {
  return (
    <button
      type="button"
      aria-label="Search"
      aria-keyshortcuts="Control+K Meta+K"
      onClick={() => {
        uiStore.trigger.closeQuickAdd();
        uiStore.trigger.toggleSearch({ open: true });
      }}
      className="inline-flex min-h-11 min-w-11 items-center justify-center gap-2 rounded-full text-muted hover:bg-surface-muted hover:text-text md:min-h-9 md:rounded-lg md:border md:border-border md:bg-surface md:px-3 md:text-sm"
    >
      <Icon name="search" />
      <span aria-hidden="true" className="hidden md:inline">
        Search
      </span>
      <kbd
        aria-hidden="true"
        className="hidden rounded border border-border px-1.5 font-sans text-xs text-muted md:inline"
      >
        {modK()}
      </kbd>
    </button>
  );
}

function ReviewLink() {
  const count = useQuery(reviewCountQuery()).data?.count ?? 0;
  return (
    <Link
      to="/review"
      aria-label={`Review: ${String(count)} waiting`}
      className={`relative ${ROUND}`}
    >
      <Icon name="bell" />
      {count > 0 && (
        <span
          aria-hidden="true"
          className="absolute top-0.5 right-0.5 min-w-4 rounded-full bg-accent px-1 text-center text-[0.625rem] leading-4 font-semibold text-accent-contrast tabular-nums md:-top-0.5 md:-right-0.5"
        >
          {count > 99 ? "99+" : count}
        </span>
      )}
    </Link>
  );
}

const SHORTCUTS: readonly (readonly [string, () => string])[] = [
  ["Search", modK],
  ["Quick add", () => "/"],
  ["Close a menu or dialog", () => "Esc"],
];

function HelpButton() {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const buttonRef = useRef<HTMLButtonElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (
        event.target instanceof Node &&
        wrapRef.current?.contains(event.target)
      )
        return;
      setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
    };
  }, [open]);

  return (
    <div
      ref={wrapRef}
      className="relative"
      onKeyDown={(event) => {
        if (event.key !== "Escape" || !open) return;
        event.preventDefault();
        setOpen(false);
        buttonRef.current?.focus();
      }}
    >
      <button
        ref={buttonRef}
        type="button"
        aria-label="Help"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => {
          setOpen((was) => !was);
        }}
        className={ROUND}
      >
        <Icon name="help" />
      </button>
      {open && (
        <section
          id={panelId}
          aria-label="Keyboard shortcuts"
          className="absolute right-0 z-50 mt-2 w-64 rounded-lg border border-border bg-surface p-4 text-sm shadow-lg"
        >
          <p className="pb-2 text-xs font-semibold text-muted uppercase">
            Keyboard shortcuts
          </p>
          <dl className="flex flex-col gap-2">
            {SHORTCUTS.map(([action, keys]) => (
              <div key={action} className="flex items-center justify-between">
                <dt>{action}</dt>
                <dd>
                  <kbd className="rounded border border-border px-1.5 font-sans text-xs text-muted">
                    {keys()}
                  </kbd>
                </dd>
              </div>
            ))}
          </dl>
        </section>
      )}
    </div>
  );
}

const THEMES: readonly (readonly [ThemeChoice, string])[] = [
  ["light", "Light"],
  ["dark", "Dark"],
  ["system", "System"],
];

function AccountMenu() {
  const theme = useSelector(uiStore, (s) => s.context.theme);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const account = useQuery({ ...accountQuery(), enabled: open });

  const signOut = async () => {
    try {
      await apiWrite<undefined>({
        kind: "create",
        method: "POST",
        path: "/auth/logout",
        idempotencyKey: crypto.randomUUID(),
      });
    } catch (error) {
      // 401: the session had already ended (expired, or revoked elsewhere), so this
      // device is signed out all the same; anything else leaves it signed in.
      if (!(error instanceof ApiError && error.status === 401)) {
        uiStore.trigger.showNotice({
          text: "Sign out did not work. Try again.",
        });
        return;
      }
    }
    queryClient.clear();
    await navigate({ to: "/login", replace: true });
  };

  return (
    <MenuButton
      label="Account"
      buttonClassName={ROUND}
      button={
        <span className="inline-flex size-8 items-center justify-center rounded-full bg-accent-soft text-accent">
          <Icon name="user" />
        </span>
      }
      onOpenChange={setOpen}
      header={
        <p className="truncate px-3 pt-1 pb-2 text-xs text-muted">
          {account.data?.email ?? "Signed in"}
        </p>
      }
      sections={[
        {
          items: [
            {
              label: "Settings",
              onSelect: () => {
                void navigate({
                  to: "/settings/$section",
                  params: { section: "account" },
                });
              },
            },
          ],
        },
        {
          label: "Theme",
          items: THEMES.map(([choice, label]) => ({
            label,
            checked: theme === choice,
            onSelect: () => {
              uiStore.trigger.setTheme({ theme: choice });
            },
          })),
        },
        {
          // P1-18, J7: the day-close panel, at any hour (the dashboard shows its own
          // button from 16:00).
          items: [
            {
              label: "Close the day",
              onSelect: () => {
                void navigate({ to: "/", search: { panel: "close" } });
              },
            },
          ],
        },
        {
          items: [
            {
              label: "Sign out",
              onSelect: () => {
                void signOut();
              },
            },
          ],
        },
      ]}
    />
  );
}

export function AppHeader() {
  const navOpen = useSelector(uiStore, (s) => s.context.navOpen);
  return (
    <header className="sticky top-0 z-30 flex h-(--tg-header-h) shrink-0 items-center gap-1 border-b border-border bg-surface/90 px-2 backdrop-blur md:gap-2 md:px-8">
      <button
        id={MENU_BUTTON_ID}
        type="button"
        aria-label="Open menu"
        aria-haspopup="dialog"
        aria-expanded={navOpen}
        onClick={() => {
          uiStore.trigger.setNavOpen({ open: true });
        }}
        className={`${ROUND} md:hidden`}
      >
        <Icon name="menu" />
      </button>
      <div className="flex-1" />
      <SearchButton />
      <ReviewLink />
      <HelpButton />
      <div aria-hidden="true" className="mx-1 h-6 w-px bg-border" />
      <AccountMenu />
    </header>
  );
}
