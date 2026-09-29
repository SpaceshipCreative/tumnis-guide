// Device UI state (P0-22). Spec stub.
import { createStore } from "@xstate/store";

export const LAST_VIEW_KEY = "tumnis.lastView";

interface UiContext {
  quickAddOpen: boolean;
  searchOpen: boolean;
  contextSheetOpen: boolean;
  railSection: string | null;
  dragging: string | null;
  lastView: Record<string, string>;
  conflict: null | { entity: string };
}

const initial: UiContext = {
  quickAddOpen: false,
  searchOpen: false,
  contextSheetOpen: false,
  railSection: null,
  dragging: null,
  lastView: {},
  conflict: null,
};

export function createUiStore() {
  return createStore({
    context: initial,
    on: {
      openQuickAdd: (c) => c,
      closeQuickAdd: (c) => c,
      toggleSearch: (c, e: { open?: boolean }) => ({ ...c, stub: e }),
      setRail: (c, e: { section: string | null }) => ({ ...c, stub: e }),
      setLastView: (c, e: { projectId: string; view: string }) => ({
        ...c,
        stub: e,
      }),
      showConflict: (c, e: { entity: string }) => ({ ...c, stub: e }),
      clearConflict: (c) => c,
    },
  });
}

export const uiStore = createUiStore();
