// The app shell (P0-22, UX 11; DS-01's Mosaic-style layout): skip link, the collapsible
// sidebar on a laptop, the header (search, review, help, account), the bottom bar and the
// navigation drawer on a phone, the page in `main`, the conflict notice, notices and the
// Undo toast (P0-24), all inside the quick-add host (P0-25: the offline queue, quick add,
// search, shortcuts), the live socket (P0-22), the app-open ping (P1-18) and the focus bar (P2-15). While the drawer is open everything else is inert. Sign-in and
// setup get the bare frame (no navigation before a session exists).
import { Outlet, useRouterState } from "@tanstack/react-router";
import { useSelector } from "@xstate/store-react";

import { PUBLIC_PATHS } from "../../lib/session";
import { uiStore } from "../../stores/uiStore";
import { FocusBar } from "../focus/FocusBar";
import { QuickAddHost } from "../quickadd/QuickAddHost";
import { AppHeader } from "./AppHeader";
import { BottomBar } from "./BottomBar";
import { ConflictToast } from "./ConflictToast";
import { NavDrawer } from "./NavDrawer";
import { NavRail } from "./NavRail";
import { NoticeToast } from "./NoticeToast";
import { MAIN_ID, SkipLink } from "./SkipLink";
import { UndoToast } from "./UndoToast";
import { useAppOpenPing } from "./useAppOpenPing";
import { useLiveSocket } from "./useLiveSocket";

export function AppShell() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const navOpen = useSelector(uiStore, (s) => s.context.navOpen);
  const bare = PUBLIC_PATHS.has(pathname);
  useAppOpenPing(!bare);
  useLiveSocket(!bare); // the live socket only once signed in (APP-11)
  return (
    <div className="min-h-dvh bg-bg text-text">
      {/* Everything but the drawer: inert while the drawer is open, so nothing behind it
          (page, toasts, Quick add) takes a click or focus. */}
      <div inert={!bare && navOpen}>
        <QuickAddHost>
          <div className="min-h-dvh md:flex">
            <SkipLink />
            {!bare && <NavRail />}
            <div className="flex min-w-0 flex-1 flex-col">
              {!bare && <AppHeader />}
              {/* Before main, so the keyboard meets the navigation first, as on a laptop. */}
              {!bare && <BottomBar />}
              {/* The focus bar (P2-15): under the header on every signed-in page. */}
              {!bare && <FocusBar />}
              <main
                id={MAIN_ID}
                tabIndex={-1}
                className="min-w-0 flex-1 px-4 pt-4 pb-24 md:px-8 md:pb-8"
              >
                <Outlet />
              </main>
            </div>
          </div>
          <ConflictToast />
          {!bare && <UndoToast />}
          <NoticeToast />
        </QuickAddHost>
      </div>
      {!bare && <NavDrawer />}
    </div>
  );
}
