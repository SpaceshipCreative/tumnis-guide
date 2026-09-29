// Board move math (P0-24, FR-3.2): the new rank sits between the new neighbours, the
// moved card excluded; an empty column starts at the first key.
import { expect, test } from "vitest";

import { between } from "../../lib/rank";
import { makeBoard } from "../../test/factories";
import { planMove } from "./move";

test("[P0-24][FR-3.2] T-P0-24-03 planMove ranks between the new neighbours", () => {
  const board = makeBoard({
    columns: [
      {
        name: "Backlog",
        status: "backlog",
        cards: [
          { title: "A", version: 3 },
          { title: "B", version: 4 },
          { title: "C", version: 5 },
        ],
      },
      { name: "Today", status: "today", cards: [] },
      {
        name: "In progress",
        status: "in_progress",
        cards: [{ title: "X" }, { title: "Y" }, { title: "Z" }],
      },
    ],
  });
  const [backlog, today, doing] = board.columns;
  if (!backlog || !today || !doing) throw new Error("three columns");
  const ranks = doing.cards.map((c) => c.task.board_rank);
  expect(ranks).toEqual(["a0", "a1", "a2"]);
  const b = backlog.cards[1]?.task;
  if (!b) throw new Error("card B");

  // Top, middle and bottom of another column.
  expect(planMove(board, b.id, doing.id, 0)).toEqual({
    taskId: b.id,
    columnId: doing.id,
    boardRank: between(null, "a0"),
    version: 4,
  });
  expect(planMove(board, b.id, doing.id, 1).boardRank).toBe(
    between("a0", "a1"),
  );
  expect(planMove(board, b.id, doing.id, 3).boardRank).toBe(
    between("a2", null),
  );

  // An empty column.
  expect(planMove(board, b.id, today.id, 0)).toEqual({
    taskId: b.id,
    columnId: today.id,
    boardRank: "a0",
    version: 4,
  });

  // Within its own column the card is not its own neighbour: B (a1) to the top sits
  // before A (a0); A to index 1 sits between B (a1) and C (a2).
  const a = backlog.cards[0]?.task;
  if (!a) throw new Error("card A");
  expect(planMove(board, b.id, backlog.id, 0).boardRank).toBe(
    between(null, "a0"),
  );
  expect(planMove(board, a.id, backlog.id, 1)).toEqual({
    taskId: a.id,
    columnId: backlog.id,
    boardRank: between("a1", "a2"),
    version: 3,
  });
});
