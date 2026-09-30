// IndexedDB for the offline queue (P0-25, FR-3.10), through `idb`
// (https://github.com/jakearchibald/idb). One database, `tumnis`, with the
// `offlineQueue` store keyed by idempotency key and read in capture order. Where
// IndexedDB is blocked (some private modes) the store runs in memory: capture still
// works online, and `persistent()` says offline capture is off.
import { type DBSchema, type IDBPDatabase, openDB } from "idb";

import type { QueueItem } from "../machines/offlineQueue";

export const IDB_NAME = "tumnis";
const STORE = "offlineQueue";

interface TumnisDB extends DBSchema {
  offlineQueue: {
    key: string;
    value: QueueItem;
    indexes: { createdAt: number };
  };
}

type Db = IDBPDatabase<TumnisDB>;

let opening: Promise<Db | null> | null = null;
let blocked = false;
const memory = new Map<string, QueueItem>();

function open(): Promise<Db | null> {
  opening ??= openDB<TumnisDB>(IDB_NAME, 1, {
    upgrade(db) {
      db.createObjectStore(STORE, { keyPath: "idempotencyKey" }).createIndex(
        "createdAt",
        "createdAt",
      );
    },
  }).catch(() => {
    blocked = true;
    return null;
  });
  return opening;
}

export const idbQueue = {
  all: async (): Promise<QueueItem[]> => {
    const db = await open();
    if (!db) {
      return [...memory.values()].sort((a, b) => a.createdAt - b.createdAt);
    }
    return db.getAllFromIndex(STORE, "createdAt");
  },
  put: async (item: QueueItem): Promise<void> => {
    const db = await open();
    if (!db) {
      memory.set(item.idempotencyKey, item);
      return;
    }
    await db.put(STORE, item);
  },
  delete: async (key: string): Promise<void> => {
    const db = await open();
    if (!db) {
      memory.delete(key);
      return;
    }
    await db.delete(STORE, key);
  },
  has: async (key: string): Promise<boolean> => {
    const db = await open();
    if (!db) return memory.has(key);
    return (await db.count(STORE, key)) > 0;
  },
};

/** False once IndexedDB has refused to open: the queue only lives in this page. */
export function persistent(): boolean {
  return !blocked;
}

/** Closes the open connection, so the database can be deleted (tests). */
export async function closeIdb(): Promise<void> {
  const db = await opening;
  db?.close();
  opening = null;
  blocked = false;
  memory.clear();
}
