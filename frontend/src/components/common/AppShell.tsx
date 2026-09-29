// The app shell (P0-22, UX 11): skip link, the rail on a laptop or the bottom bar on a
// phone, the page in `main`, and the conflict notice. Sign-in and setup get the bare
// frame (no navigation before a session exists).
import { Outlet, useRouterState } from "@tanstack/react-router";

import { PUBLIC_PATHS } from "../../lib/session";
import { BottomBar } from "./BottomBar";
import { ConflictToast } from "./ConflictToast";
import { NavRail } from "./NavRail";
import { MAIN_ID, SkipLink } from "./SkipLink";

export function AppShell() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const bare = PUBLIC_PATHS.has(pathname);
  return (
    <div className="min-h-dvh bg-bg text-text md:flex">
      <SkipLink />
      {!bare && <NavRail />}
      <main
        id={MAIN_ID}
        tabIndex={-1}
        className="min-w-0 flex-1 px-4 pt-4 pb-24 md:px-8 md:pb-8"
      >
        <Outlet />
      </main>
      {!bare && <BottomBar />}
      <ConflictToast />
    </div>
  );
}
