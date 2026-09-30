// The app shell (P0-22, UX 11; DS-01's Mosaic-style layout): skip link, the collapsible
// sidebar on a laptop, the header (search, review, help, account), the bottom bar and the
// navigation drawer on a phone, the page in `main`, the conflict notice, notices and the
// Undo toast (P0-24), all inside the quick-add host (P0-25: the offline queue, quick add,
// search, shortcuts). While the drawer is open the rest of the shell is inert. Sign-in and
// setup get the bare frame (no navigation before a session exists).
import { Outlet, useRouterState } from "@tanstack/react-router";
import { useSelector } from "@xstate/store-react";

import { PUBLIC_PATHS } from "../../lib/session";
import { uiStore } from "../../stores/uiStore";
import { QuickAddHost } from "../quickadd/QuickAddHost";
import { AppHeader } from "./AppHeader";
import { BottomBar } from "./BottomBar";
import { ConflictToast } from "./ConflictToast";
import { NavDrawer } from "./NavDrawer";
import { NavRail } from "./NavRail";
import { NoticeToast } from "./NoticeToast";
import { MAIN_ID, SkipLink } from "./SkipLink";
import { UndoToast } from "./UndoToast";

export function AppShell() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const navOpen = useSelector(uiStore, (s) => s.context.navOpen);
  const bare = PUBLIC_PATHS.has(pathname);
  return (
    <QuickAddHost>
      <div className="min-h-dvh bg-bg text-text">
        <div inert={!bare && navOpen} className="min-h-dvh md:flex">
          <SkipLink />
          {!bare && <NavRail />}
          <div className="flex min-w-0 flex-1 flex-col">
            {!bare && <AppHeader />}
            {/* Before main, so the keyboard meets the navigation first, as on a laptop. */}
            {!bare && <BottomBar />}
            <main
              id={MAIN_ID}
              tabIndex={-1}
              className="min-w-0 flex-1 px-4 pt-4 pb-24 md:px-8 md:pb-8"
            >
              <Outlet />
            </main>
          </div>
        </div>
        {!bare && <NavDrawer />}
        <ConflictToast />
        {!bare && <UndoToast />}
        <NoticeToast />
      </div>
    </QuickAddHost>
  );
}
