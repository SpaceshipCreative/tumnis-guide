// The board (P0-24, FR-3.2, UX 7): the server's layout (`GET /v1/projects/{id}/board`)
// in columns; a drag by mouse, touch (after a 250 ms press) or keyboard, or the card's
// Move menu, sends one move request (`planMove`, optimistic, rolled back on a refusal).
//
// A pointer drop is placed where the pointer is (tracked here while dragging), not at
// dnd-kit's last `over`, which one fast pointer move can leave behind; a keyboard drop
// goes where the keyboard put it (`over`), which SettledKeyboardSensor lets settle after
// each key.
import {
  closestCorners,
  DndContext,
  MouseSensor,
  TouchSensor,
  useSensor,
  useSensors,
  type Active,
  type DragEndEvent,
  type DragStartEvent,
  type Over,
} from "@dnd-kit/core";
import { sortableKeyboardCoordinates } from "@dnd-kit/sortable";
import { useQuery } from "@tanstack/react-query";
import { startTransition, useEffect, useRef, useState } from "react";

import { useMoveTask } from "../project/mutations";
import { boardQuery } from "../project/queries";
import type { Board } from "../project/types";
import { BoardColumn, COLUMN_PREFIX } from "./BoardColumn";
import {
  SettledKeyboardSensor,
  type SettledKeyboardSensorOptions,
} from "./keyboard";
import { planMove } from "./move";

interface Drop {
  columnId: string;
  index: number;
}

interface Point {
  x: number;
  y: number;
}

function pointOf(event: Event): Point | null {
  if (typeof TouchEvent !== "undefined" && event instanceof TouchEvent) {
    const touch = event.touches[0] ?? event.changedTouches[0];
    return touch ? { x: touch.clientX, y: touch.clientY } : null;
  }
  if (event instanceof MouseEvent)
    return { x: event.clientX, y: event.clientY };
  return null;
}

/** The column and the index under a pointer, from the rendered columns and cards. */
function dropAtPoint(
  root: HTMLElement,
  point: Point,
  taskId: string,
): Drop | null {
  let best: { el: HTMLElement; distance: number } | null = null;
  for (const el of root.querySelectorAll<HTMLElement>("[data-board-column]")) {
    const r = el.getBoundingClientRect();
    const dx = Math.max(r.left - point.x, 0, point.x - r.right);
    const dy = Math.max(r.top - point.y, 0, point.y - r.bottom);
    const distance = Math.hypot(dx, dy);
    if (!best || distance < best.distance) best = { el, distance };
  }
  const columnId = best?.el.dataset.boardColumn;
  if (!best || !columnId) return null;
  const cards = [
    ...best.el.querySelectorAll<HTMLElement>("[data-board-card]"),
  ].filter((c) => c.dataset.taskId !== taskId);
  const index = cards.filter((c) => {
    const r = c.getBoundingClientRect();
    return r.top + r.height / 2 < point.y;
  }).length;
  return { columnId, index };
}

function columnOfId(
  board: Board,
  id: string,
): Board["columns"][number] | undefined {
  if (id.startsWith(COLUMN_PREFIX)) {
    return board.columns.find((c) => c.id === id.slice(COLUMN_PREFIX.length));
  }
  return board.columns.find((c) => c.cards.some((card) => card.task.id === id));
}

/** Where the keyboard (or a slow pointer) left the card: `over` a card or a column. */
function dropAtOver(
  board: Board,
  active: Active,
  over: Over | null,
): Drop | null {
  if (!over) return null;
  const overId = String(over.id);
  const column = columnOfId(board, overId);
  if (!column) return null;
  if (overId.startsWith(COLUMN_PREFIX)) {
    const others = column.cards.filter((c) => c.task.id !== active.id);
    return { columnId: column.id, index: others.length };
  }
  const index = column.cards.findIndex((c) => c.task.id === overId);
  return { columnId: column.id, index: Math.max(index, 0) };
}

function titleOf(board: Board, taskId: string): string {
  for (const column of board.columns) {
    const card = column.cards.find((c) => c.task.id === taskId);
    if (card) return card.task.title;
  }
  return "The task";
}

export function BoardView({
  projectId,
  onOpen,
}: {
  projectId: string;
  onOpen: (taskId: string) => void;
}) {
  const board = useQuery(boardQuery(projectId));
  const move = useMoveTask();
  const root = useRef<HTMLDivElement>(null);
  const pointer = useRef<Point | null>(null);
  const stopTracking = useRef<(() => void) | null>(null);
  const sensors = useSensors(
    useSensor(MouseSensor),
    useSensor(TouchSensor, {
      activationConstraint: { delay: 250, tolerance: 5 },
    }),
    useSensor(SettledKeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
      // On a phone an arrow key scrolls the board to the next column instead of moving
      // the card; an instant scroll lets that land before the next key is handled.
      scrollBehavior: "auto",
      // The sensor holds a drop until `over` agrees with this detection (keyboard.ts).
      collisionDetection: closestCorners,
    } satisfies SettledKeyboardSensorOptions),
  );

  // The first render of a full board (hundreds of sortable cards) runs as a transition,
  // which React renders in short slices, rather than in the one long task a query result
  // gets (PERF-2). Later updates (moves, live changes) render as before.
  const ready = board.data !== undefined;
  const [shown, setShown] = useState(false);
  useEffect(() => {
    if (ready && !shown) {
      startTransition(() => {
        setShown(true);
      });
    }
  }, [ready, shown]);

  const loading = <p className="text-muted">Loading the board…</p>;
  if (board.isPending) return loading;
  if (board.isError) {
    return (
      <p role="alert" className="text-danger">
        The board could not be loaded.
      </p>
    );
  }
  if (!shown) return loading;
  const data = board.data;
  const columns = data.columns.map((c) => ({ id: c.id, name: c.name }));

  const send = (taskId: string, drop: Drop) => {
    const from = columnOfId(data, taskId);
    const at = from?.cards.findIndex((c) => c.task.id === taskId) ?? -1;
    if (from?.id === drop.columnId && at === drop.index) return; // nothing moved
    move.mutate({
      projectId,
      plan: planMove(data, taskId, drop.columnId, drop.index),
    });
  };

  const onDragStart = ({ activatorEvent }: DragStartEvent) => {
    pointer.current = pointOf(activatorEvent);
    if (!pointer.current) return;
    const track = (event: Event) => {
      pointer.current = pointOf(event) ?? pointer.current;
    };
    const names = ["pointermove", "mousemove", "touchmove"] as const;
    for (const name of names)
      window.addEventListener(name, track, { passive: true });
    stopTracking.current = () => {
      for (const name of names) window.removeEventListener(name, track);
    };
  };

  const endTracking = () => {
    stopTracking.current?.();
    stopTracking.current = null;
    const point = pointer.current;
    pointer.current = null;
    return point;
  };

  const onDragEnd = ({ active, over }: DragEndEvent) => {
    const point = endTracking();
    const taskId = String(active.id);
    const drop =
      point && root.current
        ? dropAtPoint(root.current, point, taskId)
        : dropAtOver(data, active, over);
    if (drop) send(taskId, drop);
  };

  const columnName = (over: Over | null) =>
    over ? (columnOfId(data, String(over.id))?.name ?? "") : "";

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCorners}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onDragCancel={() => {
        endTracking();
      }}
      accessibility={{
        screenReaderInstructions: {
          draggable:
            "To move a task, press Space, move it with the arrow keys, and press Space again to drop it or Escape to cancel. Or use its Move menu.",
        },
        announcements: {
          onDragStart: ({ active }) =>
            `Picked up ${titleOf(data, String(active.id))}`,
          onDragOver: ({ active, over }) =>
            over
              ? `${titleOf(data, String(active.id))} is over ${columnName(over)}`
              : undefined,
          onDragEnd: ({ active, over }) =>
            over
              ? `${titleOf(data, String(active.id))} was dropped in ${columnName(over)}`
              : `${titleOf(data, String(active.id))} was dropped`,
          onDragCancel: ({ active }) =>
            `Moving ${titleOf(data, String(active.id))} was cancelled`,
        },
      }}
    >
      <div
        ref={root}
        className="relative -mx-4 flex snap-x snap-mandatory items-stretch gap-3 overflow-x-auto px-4 pb-2 md:mx-0 md:snap-none md:px-0"
      >
        {data.columns.map((column) => (
          <BoardColumn
            key={column.id}
            column={column}
            columns={columns}
            onMoveTo={(taskId, columnId) => {
              const target = data.columns.find((c) => c.id === columnId);
              send(taskId, {
                columnId,
                index:
                  target?.cards.filter((c) => c.task.id !== taskId).length ?? 0,
              });
            }}
            onOpen={onOpen}
          />
        ))}
      </div>
    </DndContext>
  );
}
