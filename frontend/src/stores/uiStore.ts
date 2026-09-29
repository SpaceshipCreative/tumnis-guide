// Device UI state (P0-22, ADR-0004): what is open on this device, the last view per
// project (kept in localStorage) and the conflict notice. Server data lives in Query.
import { createStore } from "@xstate/store";
import * as z from "zod";

import { safeGetItem, safeSetItem } from "../lib/storage";
import { projectViews, type ProjectView } from "../lib/views";

export const LAST_VIEW_KEY = "tumnis.lastView";

export type RailSection =
  "dashboard" | "tasks" | "review" | "search" | "settings";

export interface UiContext {
  quickAddOpen: boolean;
  searchOpen: boolean;
  contextSheetOpen: boolean;
  railSection: RailSection | null;
  dragging: string | null;
  lastView: Record<string, ProjectView>;
  conflict: null | { entity: string };
}

const viewSchema = z.enum(projectViews);

/** The stored views; unknown views and unreadable JSON are dropped. */
export function loadLastViews(): Record<string, ProjectView> {
  let raw: unknown;
  try {
    raw = JSON.parse(safeGetItem(LAST_VIEW_KEY) ?? "{}");
  } catch {
    return {};
  }
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return {};
  return Object.fromEntries(
    Object.entries(raw).flatMap(([projectId, view]) => {
      const parsed = viewSchema.safeParse(view);
      return parsed.success ? [[projectId, parsed.data]] : [];
    }),
  );
}

export function createUiStore() {
  const initial: UiContext = {
    quickAddOpen: false,
    searchOpen: false,
    contextSheetOpen: false,
    railSection: null,
    dragging: null,
    lastView: loadLastViews(),
    conflict: null,
  };
  const store = createStore({
    context: initial,
    on: {
      openQuickAdd: (c) => ({ ...c, quickAddOpen: true }),
      closeQuickAdd: (c) => ({ ...c, quickAddOpen: false }),
      toggleSearch: (c, e: { open?: boolean }) => ({
        ...c,
        searchOpen: e.open ?? !c.searchOpen,
      }),
      setRail: (c, e: { section: RailSection | null }) => ({
        ...c,
        railSection: e.section,
      }),
      setLastView: (c, e: { projectId: string; view: ProjectView }) => ({
        ...c,
        lastView: { ...c.lastView, [e.projectId]: e.view },
      }),
      showConflict: (c, e: { entity: string }) => ({
        ...c,
        conflict: { entity: e.entity },
      }),
      clearConflict: (c) => ({ ...c, conflict: null }),
    },
  });
  let stored = initial.lastView;
  store.subscribe((snapshot) => {
    if (snapshot.context.lastView === stored) return;
    stored = snapshot.context.lastView;
    safeSetItem(LAST_VIEW_KEY, JSON.stringify(stored));
  });
  return store;
}

export const uiStore = createUiStore();
