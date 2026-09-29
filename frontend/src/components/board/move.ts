/* eslint-disable @typescript-eslint/no-unused-vars -- a P0-24 spec stub */
// Board move math (P0-24, FR-3.2, R-20): one drag is one write, the task's new column and
// a rank between its new neighbours' ranks (lib/rank.ts), with the version read.
import type { BoardOut } from "../../api/types.gen";

export interface MovePlan {
  taskId: string;
  columnId: string;
  boardRank: string;
  version: number;
}

export function planMove(
  _board: BoardOut,
  _taskId: string,
  _toColumnId: string,
  _toIndex: number,
): MovePlan {
  throw new Error("not implemented: P0-24");
}
