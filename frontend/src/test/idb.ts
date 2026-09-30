// A fresh IndexedDB per test (P0-25): setup.ts imports fake-indexeddb/auto, and this
// closes the app's connection and deletes the `tumnis` database after each test.
import { closeIdb, IDB_NAME } from "../lib/idb";

export async function resetIdb(): Promise<void> {
  await closeIdb();
  await new Promise<void>((resolve, reject) => {
    const request = indexedDB.deleteDatabase(IDB_NAME);
    request.onsuccess = () => {
      resolve();
    };
    request.onerror = () => {
      reject(request.error ?? new Error("deleteDatabase failed"));
    };
  });
}
