// One board card (P0-24, FR-3.2, UX 7): sortable by mouse, touch and keyboard (Space
// picks it up, arrows move it, Space drops it), and a Move menu that sends the same one
// move request without dragging (the phone's and a screen reader's path).
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { useEffect, useRef, useState, type SyntheticEvent } from "react";

import { formatMinutes } from "../dashboard/format";
import type { Board } from "../project/types";
import { Checklist } from "./Checklist";

type Card = Board["columns"][number]["cards"][number];

const LABEL_TEXT = { human: "Human", ai: "AI", hybrid: "Hybrid" } as const;

// Buttons inside the card must not start a drag.
const stop = (event: SyntheticEvent) => {
  event.stopPropagation();
};

function MoveMenu({
  title,
  targets,
  onMove,
  onOpen,
}: {
  title: string;
  targets: { id: string; name: string }[];
  onMove: (columnId: string) => void;
  onOpen: () => void;
}) {
  const [open, setOpen] = useState(false);
  const menu = useRef<HTMLDivElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (open)
      menu.current?.querySelector<HTMLElement>("[role=menuitem]")?.focus();
  }, [open]);
  return (
    <div
      className="relative flex gap-1"
      onMouseDown={stop}
      onTouchStart={stop}
      onKeyDown={stop}
    >
      <button
        type="button"
        aria-label={`Open ${title}`}
        onClick={onOpen}
        className="min-h-11 rounded border border-border px-2 text-xs md:min-h-7"
      >
        Open
      </button>
      <button
        ref={toggle}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => {
          setOpen((o) => !o);
        }}
        className="min-h-11 rounded border border-border px-2 text-xs md:min-h-7"
      >
        Move
      </button>
      {open && (
        <div
          ref={menu}
          role="menu"
          aria-label={`Move ${title} to`}
          className="absolute top-full right-0 z-20 mt-1 flex min-w-40 flex-col rounded-md border border-border bg-surface py-1 shadow-lg"
          onKeyDown={(event) => {
            const items = [
              ...event.currentTarget.querySelectorAll<HTMLElement>(
                "[role=menuitem]",
              ),
            ];
            const at = items.indexOf(document.activeElement as HTMLElement);
            if (event.key === "Escape") {
              // Back to the Move button, not the page: the menu unmounts.
              setOpen(false);
              toggle.current?.focus();
            }
            if (event.key === "ArrowDown")
              items[(at + 1) % items.length]?.focus();
            if (event.key === "ArrowUp") {
              items[(at - 1 + items.length) % items.length]?.focus();
            }
          }}
        >
          {targets.map((target) => (
            <button
              key={target.id}
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                onMove(target.id);
              }}
              className="px-3 py-2 text-left text-sm hover:bg-surface-muted"
            >
              {target.name}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function BoardCard({
  card,
  columnId,
  columns,
  onMoveTo,
  onOpen,
}: {
  card: Card;
  columnId: string;
  columns: { id: string; name: string }[];
  onMoveTo: (taskId: string, columnId: string) => void;
  onOpen: (taskId: string) => void;
}) {
  const { task } = card;
  const {
    setNodeRef,
    attributes,
    listeners,
    transform,
    transition,
    isDragging,
  } = useSortable({
    id: task.id,
    data: { columnId },
    attributes: { role: "listitem", roleDescription: "draggable task" },
  });
  const estimate =
    task.label === "ai" || task.estimate_minutes === null
      ? null
      : formatMinutes(task.estimate_minutes);
  return (
    <li
      ref={setNodeRef}
      {...attributes}
      {...listeners}
      aria-label={task.title}
      aria-roledescription="draggable task"
      aria-pressed={undefined}
      data-board-card
      data-task-id={task.id}
      data-task-title={task.title}
      data-board-rank={task.board_rank}
      data-dragging={isDragging || undefined}
      style={{
        transform: CSS.Translate.toString(transform),
        transition,
      }}
      className={`flex touch-manipulation flex-col gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-sm shadow-sm outline-none focus-visible:ring-2 focus-visible:ring-accent ${
        isDragging ? "relative z-10 opacity-80 shadow-lg" : ""
      }`}
    >
      <p className="font-medium break-words">{task.title}</p>
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted">
        <span>
          {task.label === null ? "No label yet" : LABEL_TEXT[task.label]}
          {estimate && ` · ${estimate}`}
        </span>
        <MoveMenu
          title={task.title}
          targets={columns.filter((c) => c.id !== columnId)}
          onMove={(to) => {
            onMoveTo(task.id, to);
          }}
          onOpen={() => {
            onOpen(task.id);
          }}
        />
      </div>
      {card.checklist.length > 0 && <Checklist items={card.checklist} />}
    </li>
  );
}
