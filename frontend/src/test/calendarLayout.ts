// jsdom has no layout, and dnd-kit finds drop targets from element rects. This lays the
// Calendar view's week grid out the way a browser would, from the data attributes the
// view puts on it (P1-12):
// - `data-col` (the day, 0 = Monday), `data-minute` (minutes after local midnight) and
//   `data-minutes` (length): day column `col` spans x from col * 100 to col * 100 + 100,
//   one pixel per minute down the day. Free slots, events and planned blocks carry them.
// - `data-card`: a card to schedule, stacked at x = 800, 40 px apart.
// Everything else keeps jsdom's empty rect. `pointAt(col, "HH:MM")` is a point inside
// that minute of that day.
import { vi } from "vitest";

export const COLUMN_WIDTH = 100;
const CARDS_LEFT = 800;

function rect(
  left: number,
  top: number,
  width: number,
  height: number,
): DOMRect {
  return {
    x: left,
    y: top,
    left,
    top,
    width,
    height,
    right: left + width,
    bottom: top + height,
    toJSON: () => ({}),
  };
}

function rectOf(el: HTMLElement): DOMRect {
  const { col, minute, minutes } = el.dataset;
  if (col !== undefined && minute !== undefined) {
    return rect(
      Number(col) * COLUMN_WIDTH,
      Number(minute),
      COLUMN_WIDTH,
      Number(minutes ?? 15),
    );
  }
  if (el.dataset.card !== undefined) {
    const cards = [
      ...el.ownerDocument.querySelectorAll<HTMLElement>("[data-card]"),
    ];
    return rect(CARDS_LEFT, cards.indexOf(el) * 40, 160, 30);
  }
  return rect(0, 0, 0, 0);
}

/** Gives every element the rect above until the test's mocks are restored. */
export function layOutWeek(): void {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
    function (this: HTMLElement) {
      return rectOf(this);
    },
  );
}

/** A point inside minute `hhmm` of day `col` (0 = Monday) on the laid-out grid. */
export function pointAt(col: number, hhmm: string): { x: number; y: number } {
  const [h, m] = hhmm.split(":").map(Number) as [number, number];
  return { x: col * COLUMN_WIDTH + COLUMN_WIDTH / 2, y: h * 60 + m + 5 };
}

/** The centre of the n-th card to schedule. */
export function cardPoint(index: number): { x: number; y: number } {
  return { x: CARDS_LEFT + 80, y: index * 40 + 15 };
}
