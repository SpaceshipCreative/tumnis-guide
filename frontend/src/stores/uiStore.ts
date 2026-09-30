// Device UI state (P0-22, ADR-0004): what is open on this device, the last view per
// project (kept in localStorage), the conflict notice and plain notices (P0-24). Server
// data lives in Query.
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
  /** A plain-words notice, e.g. why a board move was refused (P0-24). */
  notice: string | null;
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
    notice: null,
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
      setContextSheet: (c, e: { open: boolean }) => ({
        ...c,
        contextSheetOpen: e.open,
      }),
      showNotice: (c, e: { text: string }) => ({ ...c, notice: e.text }),
      clearNotice: (c) => ({ ...c, notice: null }),
    },
  });
  // Persist what changed on top of what storage holds now (another tab may have written
  // other projects since this one loaded), never the whole in-memory map (P0-24).
  let stored = initial.lastView;
  store.subscribe((snapshot) => {
    const next = snapshot.context.lastView;
    if (next === stored) return;
    const changed = Object.fromEntries(
      Object.entries(next).filter(([id, view]) => stored[id] !== view),
    );
    stored = next;
    safeSetItem(
      LAST_VIEW_KEY,
      JSON.stringify({ ...loadLastViews(), ...changed }),
    );
  });
  return store;
}

export const uiStore = createUiStore();
