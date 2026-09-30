// Frontend harness self tests (P0-02). They keep the Vitest layer from ever
// collecting zero tests and prove the setup file wires MSW and IndexedDB.
import { screen } from "@testing-library/react";
import { delay, http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { server } from "./msw/server";

// Loopback, so that without MSW the request fails fast instead of timing out.
const UNHANDLED_URL = "http://127.0.0.1/v1/unhandled";
const SLOW_URL = "http://localhost/v1/harness/slow";

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

// On a loaded machine the render behind a `findBy` can take longer than Testing
// Library's 1 s default. An answer that arrives after 1.5 s stands in for that: the
// harness keeps waiting for it (setup.ts, issue #76).
test("[P0-02][Quality-rule-5] issue #76 findBy waits out an answer slower than one second", async () => {
  server.use(
    http.get(SLOW_URL, async () => {
      await delay(1_500);
      return HttpResponse.json({ text: "Late answer" });
    }),
  );
  const shown = fetch(SLOW_URL).then(async (response) => {
    const { text } = (await response.json()) as { text: string };
    const note = document.createElement("p");
    note.textContent = text;
    document.body.append(note);
    return note;
  });

  expect(await screen.findByText("Late answer")).toBeInTheDocument();
  (await shown).remove();
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
