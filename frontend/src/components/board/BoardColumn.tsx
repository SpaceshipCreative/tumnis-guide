// One board column (P0-24, FR-3.2): a region named after the column, a sortable list of
// its cards, and a drop area when it is empty.
import { useDroppable } from "@dnd-kit/core";
import {
  SortableContext,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";

import type { Board } from "../project/types";
import { BoardCard } from "./BoardCard";

type Column = Board["columns"][number];

export const COLUMN_PREFIX = "column:";

export function BoardColumn({
  column,
  columns,
  onMoveTo,
  onOpen,
}: {
  column: Column;
  columns: { id: string; name: string }[];
  onMoveTo: (taskId: string, columnId: string) => void;
  onOpen: (taskId: string) => void;
}) {
  const { setNodeRef } = useDroppable({
    id: `${COLUMN_PREFIX}${column.id}`,
    data: { columnId: column.id },
  });
  return (
    <section
      ref={setNodeRef}
      aria-label={column.name}
      data-board-column={column.id}
      className="flex w-72 shrink-0 snap-start flex-col gap-2 rounded-lg bg-surface-muted p-2 md:w-64"
    >
      <h2 className="flex justify-between px-1 text-sm font-semibold">
        <span>{column.name}</span>
        <span className="font-normal text-muted">
          {column.cards.length}
          <span className="sr-only"> cards</span>
        </span>
      </h2>
      <SortableContext
        id={column.id}
        items={column.cards.map((c) => c.task.id)}
        strategy={verticalListSortingStrategy}
      >
        <ul className="flex min-h-16 flex-col gap-2">
          {column.cards.map((card) => (
            <BoardCard
              key={card.task.id}
              card={card}
              columnId={column.id}
              columns={columns}
              onMoveTo={onMoveTo}
              onOpen={onOpen}
            />
          ))}
        </ul>
      </SortableContext>
    </section>
  );
}
