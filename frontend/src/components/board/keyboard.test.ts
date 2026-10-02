// The keyboard sensor's drop waits for dnd-kit's `over` to catch up with a move (P0-24,
// UX-7). On a phone an arrow key only scrolls the board; dnd-kit then needs a scroll
// event, a render, an effect and another render before `over` names the new column, and
// on a busy machine several frames can pass first with nothing changed. A Space in that
// gap dropped the card where it was ("dropped in Backlog"): the T-P0-24-05 flake on CI.
import type {
  ClientRect,
  KeyboardCoordinateGetter,
  KeyboardSensorProps,
} from "@dnd-kit/core";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { SettledKeyboardSensor } from "./keyboard";

const rect = (left: number, top = 0): ClientRect => ({
  left,
  top,
  width: 200,
  height: 80,
  right: left + 200,
  bottom: top + 80,
});

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["requestAnimationFrame", "setTimeout"] });
});

afterEach(() => {
  vi.useRealTimers();
  document.body.innerHTML = "";
});

function frames(count: number) {
  for (let i = 0; i < count; i += 1) vi.advanceTimersToNextFrame();
}

function press(code: string) {
  document.dispatchEvent(
    new KeyboardEvent("keydown", { code, bubbles: true, cancelable: true }),
  );
}

test("[P0-24][UX-7] T-P0-24-05 flake: a Space after an arrow waits for over to reach the new column", () => {
  const card = document.createElement("li");
  document.body.append(card);
  const containers = [
    { id: "backlog-card", disabled: false },
    { id: "today-card", disabled: false },
  ];
  const context = {
    current: {
      active: { id: "backlog-card" },
      collisionRect: rect(16),
      droppableRects: new Map([
        ["backlog-card", rect(16)],
        ["today-card", rect(316)],
      ]),
      droppableContainers: { getEnabled: () => containers },
      over: { id: "backlog-card" } as { id: string } | null,
      scrollableAncestors: [] as Element[],
    },
  };
  // The phone's ArrowRight: the board scrolls one column, so the droppables move under
  // the card, and the card itself does not move.
  const scrollOneColumn: KeyboardCoordinateGetter = () => {
    context.current.droppableRects = new Map([
      ["backlog-card", rect(-284)],
      ["today-card", rect(16)],
    ]);
    return undefined;
  };
  const onEnd = vi.fn();
  const pickUp = new KeyboardEvent("keydown", { code: "Space" });
  Object.defineProperty(pickUp, "target", { value: card });

  new SettledKeyboardSensor({
    active: "backlog-card",
    activeNode: { id: "backlog-card", key: "k", node: { current: card } },
    event: pickUp,
    context: context as unknown as KeyboardSensorProps["context"],
    options: { coordinateGetter: scrollOneColumn, scrollBehavior: "auto" },
    onStart: vi.fn(),
    onMove: vi.fn(),
    onEnd,
    onCancel: vi.fn(),
    onAbort: vi.fn(),
    onPending: vi.fn(),
  } as unknown as KeyboardSensorProps);
  frames(5);

  press("ArrowRight");
  press("Space");
  // dnd-kit has not re-rendered yet: `over` still names the Backlog card.
  frames(10);
  expect(onEnd).not.toHaveBeenCalled();

  context.current.over = { id: "today-card" };
  frames(5);
  expect(onEnd).toHaveBeenCalledTimes(1);
});

/** The phone board of the test above: a card in Backlog, Today one column right. */
function phoneBoard() {
  const card = document.createElement("li");
  document.body.append(card);
  const containers = [
    { id: "backlog-card", disabled: false },
    { id: "today-card", disabled: false },
  ];
  const context = {
    current: {
      active: { id: "backlog-card" },
      collisionRect: rect(16),
      droppableRects: new Map([
        ["backlog-card", rect(16)],
        ["today-card", rect(316)],
      ]),
      droppableContainers: { getEnabled: () => containers },
      over: { id: "backlog-card" } as { id: string } | null,
      scrollableAncestors: [] as Element[],
    },
  };
  const scrollOneColumn: KeyboardCoordinateGetter = () => {
    context.current.droppableRects = new Map([
      ["backlog-card", rect(-284)],
      ["today-card", rect(16)],
    ]);
    return undefined;
  };
  const onEnd = vi.fn();
  const onCancel = vi.fn();
  const pickUp = new KeyboardEvent("keydown", { code: "Space" });
  Object.defineProperty(pickUp, "target", { value: card });
  new SettledKeyboardSensor({
    active: "backlog-card",
    activeNode: { id: "backlog-card", key: "k", node: { current: card } },
    event: pickUp,
    context: context as unknown as KeyboardSensorProps["context"],
    options: { coordinateGetter: scrollOneColumn, scrollBehavior: "auto" },
    onStart: vi.fn(),
    onMove: vi.fn(),
    onEnd,
    onCancel,
    onAbort: vi.fn(),
    onPending: vi.fn(),
  } as unknown as KeyboardSensorProps);
  frames(5);
  return { context, onEnd, onCancel };
}

test("[P0-24][UX-7] a drop never replays with a stale over, however long over lags", () => {
  const { context, onEnd } = phoneBoard();
  press("ArrowRight");
  press("Space");
  frames(120);
  expect(onEnd).not.toHaveBeenCalled();

  context.current.over = { id: "today-card" };
  frames(5);
  expect(onEnd).toHaveBeenCalledTimes(1);
});

test("[P0-24][UX-7] a Space pressed after a long lag still waits for over", () => {
  const { context, onEnd } = phoneBoard();
  press("ArrowRight");
  frames(60);
  press("Space");
  frames(10);
  expect(onEnd).not.toHaveBeenCalled();

  context.current.over = { id: "today-card" };
  frames(5);
  expect(onEnd).toHaveBeenCalledTimes(1);
});

test("[P0-24][UX-7] Escape cancels a drag whose over lags, without dropping", () => {
  const { onEnd, onCancel } = phoneBoard();
  press("ArrowRight");
  press("Space");
  press("Escape");
  frames(60);
  expect(onEnd).not.toHaveBeenCalled();
  expect(onCancel).toHaveBeenCalledTimes(1);
});
