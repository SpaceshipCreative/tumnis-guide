// The app shell (P0-22, UX 11): skip link, the rail on a laptop or the bottom bar on a
// phone, the page in `main`, the conflict notice, notices and the Undo toast (P0-24).
// Sign-in and setup get the bare frame (no navigation before a session exists).
import { Outlet, useRouterState } from "@tanstack/react-router";

import { PUBLIC_PATHS } from "../../lib/session";
import { BottomBar } from "./BottomBar";
import { ConflictToast } from "./ConflictToast";
import { NavRail } from "./NavRail";
import { NoticeToast } from "./NoticeToast";
import { MAIN_ID, SkipLink } from "./SkipLink";
import { UndoToast } from "./UndoToast";

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
      {!bare && <UndoToast />}
      <NoticeToast />
    </div>
  );
}
