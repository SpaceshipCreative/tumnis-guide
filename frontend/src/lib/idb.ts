/* eslint-disable @typescript-eslint/no-unused-vars, @typescript-eslint/require-await -- a P0-25 spec stub */
// IndexedDB for the offline queue (P0-25, FR-3.10). Arrives with its implementation.
import type { QueueItem } from "../machines/offlineQueue";

export const IDB_NAME = "tumnis";

function missing(): never {
  throw new Error("lib/idb.ts arrives with P0-25");
}

export const idbQueue = {
  all: async (): Promise<QueueItem[]> => missing(),
  put: async (_item: QueueItem): Promise<void> => {
    missing();
  },
  delete: async (_key: string): Promise<void> => {
    missing();
  },
  has: async (_key: string): Promise<boolean> => missing(),
};

/** Closes the open connection, so the database can be deleted (tests). */
export async function closeIdb(): Promise<void> {
  // nothing open yet
}
