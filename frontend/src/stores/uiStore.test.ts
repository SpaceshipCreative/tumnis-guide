// Device UI state (P0-22, ADR-0004): events move the context; the last project view
// per project survives a reload through localStorage.
import { beforeEach, expect, test } from "vitest";

import { createUiStore, LAST_VIEW_KEY } from "./uiStore";

beforeEach(() => {
  localStorage.clear();
});

test("[P0-22][ADR-0004] T-P0-22-18 ui store transitions", () => {
  const store = createUiStore();
  const context = () => store.getSnapshot().context;
  expect(context()).toMatchObject({
    quickAddOpen: false,
    searchOpen: false,
    contextSheetOpen: false,
    railSection: null,
    dragging: null,
    lastView: {},
    conflict: null,
  });

  store.trigger.openQuickAdd();
  expect(context().quickAddOpen).toBe(true);
  store.trigger.closeQuickAdd();
  expect(context().quickAddOpen).toBe(false);

  store.trigger.toggleSearch({});
  expect(context().searchOpen).toBe(true);
  store.trigger.toggleSearch({});
  expect(context().searchOpen).toBe(false);
  store.trigger.toggleSearch({ open: true });
  store.trigger.toggleSearch({ open: true });
  expect(context().searchOpen).toBe(true);

  store.trigger.setRail({ section: "review" });
  expect(context().railSection).toBe("review");
  store.trigger.setRail({ section: null });
  expect(context().railSection).toBeNull();

  store.trigger.showConflict({ entity: "task" });
  expect(context().conflict).toEqual({ entity: "task" });
  store.trigger.clearConflict();
  expect(context().conflict).toBeNull();

  const projectId = crypto.randomUUID();
  store.trigger.setLastView({ projectId, view: "board" });
  expect(context().lastView).toEqual({ [projectId]: "board" });
  expect(JSON.parse(localStorage.getItem(LAST_VIEW_KEY) ?? "{}")).toEqual({
    [projectId]: "board",
  });

  // A new store (a reload) starts from the stored views; junk is dropped.
  localStorage.setItem(
    LAST_VIEW_KEY,
    JSON.stringify({ [projectId]: "board", other: "nonsense" }),
  );
  expect(createUiStore().getSnapshot().context.lastView).toEqual({
    [projectId]: "board",
  });
  localStorage.setItem(LAST_VIEW_KEY, "{not json");
  expect(createUiStore().getSnapshot().context.lastView).toEqual({});
});
