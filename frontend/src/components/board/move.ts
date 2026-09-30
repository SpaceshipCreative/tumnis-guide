// Board move math (P0-24, FR-3.2, R-20): one drag is one write, the task's new column and
// a rank between its new neighbours' ranks (lib/rank.ts), with the version read.
import { between } from "../../lib/rank";
import type { Board } from "../project/types";

export interface MovePlan {
  taskId: string;
  columnId: string;
  boardRank: string;
  version: number;
}

/** The version of the task as the board shows it. */
export function versionOf(board: Board, taskId: string): number {
  for (const column of board.columns) {
    const card = column.cards.find((c) => c.task.id === taskId);
    if (card) return card.task.version;
  }
  throw new Error(`task ${taskId} is not on the board`);
}

export function planMove(
  board: Board,
  taskId: string,
  toColumnId: string,
  toIndex: number,
): MovePlan {
  const column = board.columns.find((c) => c.id === toColumnId);
  if (!column) throw new Error(`column ${toColumnId} is not on the board`);
  const target = column.cards.filter((c) => c.task.id !== taskId);
  const before = target[toIndex - 1]?.task.board_rank ?? null;
  const after = target[toIndex]?.task.board_rank ?? null;
  return {
    taskId,
    columnId: toColumnId,
    boardRank: between(before, after),
    version: versionOf(board, taskId),
  };
}
