// Frontend harness self tests (P0-02). They keep the Vitest layer from ever
// collecting zero tests and prove the setup file wires MSW and IndexedDB.
import { expect, test } from "vitest";

// Loopback, so that without MSW the request fails fast instead of timing out.
const UNHANDLED_URL = "http://127.0.0.1/v1/unhandled";

function rejectionOf(promise: Promise<unknown>): Promise<unknown> {
  return promise.then(
    () => undefined,
    (error: unknown) => error,
  );
}

function requestResult<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => {
      resolve(request.result);
    };
    request.onerror = () => {
      reject(request.error ?? new Error("IndexedDB request failed"));
    };
  });
}

function openHarnessDb(): Promise<IDBDatabase> {
  const request = indexedDB.open("tumnis-harness", 1);
  request.onupgradeneeded = () => {
    request.result.createObjectStore("kv");
  };
  return requestResult(request);
}

test("[P0-02][Quality-rule-5] T-P0-02-15 msw rejects unhandled requests", async () => {
  const error = await rejectionOf(fetch(UNHANDLED_URL));

  expect(error).toBeInstanceOf(TypeError);
  expect(String((error as TypeError).cause)).toMatch(
    /\[MSW\] Cannot bypass a request when using the "error" strategy/,
  );
});

test("[P0-02][Quality-rule-5] T-P0-02-15 fake-indexeddb persists across open", async () => {
  const first = await openHarnessDb();
  await requestResult(
    first.transaction("kv", "readwrite").objectStore("kv").put("kept", "k"),
  );
  first.close();

  const second = await openHarnessDb();
  const value = await requestResult<unknown>(
    second.transaction("kv", "readonly").objectStore("kv").get("k"),
  );
  second.close();

  expect(value).toBe("kept");
});
