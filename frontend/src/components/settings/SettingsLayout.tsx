// The Settings frame (P0-26, UX 11): on a laptop the section list sits beside the open
// section; on a phone the open section fills the screen and "All settings" goes back to
// the list.
import { useState, type ReactNode } from "react";

import { SettingsNav } from "./SettingsNav";

export function SettingsLayout({ children }: { children: ReactNode }) {
  const [listOpen, setListOpen] = useState(false);
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <h1 className="text-2xl font-semibold">Settings</h1>
      <div className="min-w-0 md:grid md:grid-cols-[13rem_minmax(0,1fr)] md:gap-8">
        <div className={listOpen ? "block" : "hidden md:block"}>
          <SettingsNav
            onPick={() => {
              setListOpen(false);
            }}
          />
        </div>
        <div
          className={
            listOpen ? "hidden md:block" : "flex min-w-0 flex-col gap-4"
          }
        >
          <div className="md:hidden">
            <button
              type="button"
              className="inline-flex min-h-11 items-center gap-1 rounded-md px-2 font-medium text-accent"
              onClick={() => {
                setListOpen(true);
              }}
            >
              <span aria-hidden="true">‹</span> All settings
            </button>
          </div>
          {children}
        </div>
      </div>
    </div>
  );
}
