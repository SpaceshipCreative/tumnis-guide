// Device UI state (P0-22, ADR-0004): what is open on this device, the last view per
// project (kept in localStorage), the conflict notice and plain notices (P0-24), and the
// shell's colour theme, sidebar width and phone drawer (DS-01: the theme and the sidebar
// are kept in localStorage, the drawer never is). Server data lives in Query.
import { createStore } from "@xstate/store";
import * as z from "zod";

import { safeGetItem, safeSetItem } from "../lib/storage";
import {
  applyTheme,
  loadTheme,
  saveTheme,
  type ThemeChoice,
} from "../lib/theme";
import { projectViews, type ProjectView } from "../lib/views";

export const LAST_VIEW_KEY = "tumnis.lastView";
/** Whether the laptop sidebar shows icons only (DS-01), remembered per device. */
export const SIDEBAR_KEY = "tumnis.sidebarCollapsed";

export type RailSection =
  "dashboard" | "tasks" | "review" | "search" | "settings";

/** The project Context rail's sections (P0-24; RailSections opens one at a time). */
export type ContextSection =
  "brief" | "knowledge" | "connections" | "schedule" | "agent" | "settings";

export interface UiContext {
  quickAddOpen: boolean;
  searchOpen: boolean;
  contextSheetOpen: boolean;
  /** A Context section to open next, e.g. Knowledge after a file is dropped on the
   * composer; the Context sections take it when they show and clear it. */
  contextRequest: ContextSection | null;
  railSection: RailSection | null;
  dragging: string | null;
  lastView: Record<string, ProjectView>;
  conflict: null | { entity: string };
  /** A plain-words notice, e.g. why a board move was refused (P0-24). */
  notice: string | null;
  /** The colour theme on this device (DS-01). */
  theme: ThemeChoice;
  /** The laptop sidebar shows icons only (DS-01). */
  sidebarCollapsed: boolean;
  /** The phone's navigation drawer is open (DS-01). */
  navOpen: boolean;
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
    contextRequest: null,
    railSection: null,
    dragging: null,
    lastView: loadLastViews(),
    conflict: null,
    notice: null,
    theme: loadTheme(),
    sidebarCollapsed: safeGetItem(SIDEBAR_KEY) === "true",
    navOpen: false,
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
      requestContextSection: (c, e: { section: ContextSection }) => ({
        ...c,
        contextRequest: e.section,
      }),
      clearContextRequest: (c) => ({ ...c, contextRequest: null }),
      showNotice: (c, e: { text: string }) => ({ ...c, notice: e.text }),
      clearNotice: (c) => ({ ...c, notice: null }),
      setTheme: (c, e: { theme: ThemeChoice }) => ({ ...c, theme: e.theme }),
      setSidebarCollapsed: (c, e: { collapsed: boolean }) => ({
        ...c,
        sidebarCollapsed: e.collapsed,
      }),
      setNavOpen: (c, e: { open: boolean }) => ({ ...c, navOpen: e.open }),
    },
  });
  // Persist what changed on top of what storage holds now (another tab may have written
  // other projects since this one loaded), never the whole in-memory map (P0-24).
  let stored = initial.lastView;
  let theme = initial.theme;
  let collapsed = initial.sidebarCollapsed;
  applyTheme(theme);
  store.subscribe(({ context }) => {
    if (context.theme !== theme) {
      theme = context.theme;
      saveTheme(theme);
      applyTheme(theme);
    }
    if (context.sidebarCollapsed !== collapsed) {
      collapsed = context.sidebarCollapsed;
      safeSetItem(SIDEBAR_KEY, String(collapsed));
    }
    const next = context.lastView;
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
